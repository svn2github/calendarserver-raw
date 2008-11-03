##
# Copyright (c) 2005-2008 Apple Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##

"""
Localization module

How to use:

    from __future__ import with_statement
    from localization import translationTo

    with translationTo('de'):
        print _("Hello")
        print _("The event will last %(days)d days") % { 'days' : 4 }

    ... Hallo
    ... Die Veranstaltung dauert 4 Tage

Before you can actually get translated text, you need to:

    1) Choose a "domain" for your code, such as 'calendarserver'
    2) Run pygettext.py on your source to generate a <domain>.pot file.
       pygettext.py scans the source for _( ) and copies those strings to the
       .pot.
    3) For each language, copy the .pot file to .po and give it to the person
       who is doing the translation for editing
    4) Run msgfmt.py on the translated .po to generate a binary .mo
    5) Put the .mo into locales/<lang>/LC_MESSAGES/<domain>.mo

The German .po file for the above example would look like:

    msgid "Hello"
    msgstr "Hallo"

    msgid "The event will last %(days)d days"
    msgstr "Die Veranstaltung dauert %(days)d Tage"

The transationTo class automatically binds '_' to the appropriate translation
function for the duration of the "with" context.  It's smart enough to allow
nesting of "with" contexts, as in:

    with translationTo('de'):
        print _("Hello") # in German

        with translationTo('fr'):
            print _("Hello") # in French

        print _("Hello") # in German

If a translation file cannot be found for the specified language, it will fall
back to 'en'.  If 'en' can't be found, gettext will raise IOError.

If you use the with/as form, you will get an object that implements some
helper methods for date formatting:

    with translationTo('en') as trans:
        print trans.dtDate(datetime.today())

    ... Thursday, October 23, 2008

    with translationTo('fr') as trans:
        print trans.dtDate(datetime.today())

    ... Jeudi, Octobre 23, 2008

The .po files contain localizable strings for month and day names, as well as
date format strings, in case a locale likes these values in a different order
or with different punctuation.


TODO: recurrence
"""


import gettext
import inspect
import datetime
from twistedcaldav.config import config


class translationTo(object):

    translations = {}

    def __init__(self, lang, domain='calendarserver', localeDir=None):

        if localeDir is None:
            localeDir = config.Localization["LocalesDirectory"]

        # Cache gettext translation objects in class.translations
        key = (lang, domain, localeDir)
        self.translation = self.translations.get(key, None)
        if self.translation is None:
            self.translation = gettext.translation(domain=domain,
                localedir=localeDir, languages=[lang, 'en'], fallback=True)
            self.translations[key] = self.translation

    def __enter__(self):
        # Get the caller's globals so we can rebind their '_' to our translator
        caller_globals = inspect.stack()[1][0].f_globals

        # Store whatever '_' is already bound to so we can restore it later
        if caller_globals.has_key('_'):
            self.prev = caller_globals['_']

        # Rebind '_' to our translator
        caller_globals['_'] = self.translation.ugettext

        # What we return here is accessible to the caller via the 'as' clause
        return self

    def __exit__(self, type, value, traceback):
        # Restore '_' if it previously had a value
        if hasattr(self, 'prev'):
            inspect.stack()[1][0].f_globals['_'] = self.prev

        # Don't swallow exceptions
        return False

    def date(self, component):
        dtStart = component.propertyNativeValue("DTSTART")
        return self.dtDate(dtStart)

    def time(self, component):
        """
        Examples:

        3:30 PM to 4:30 PM PDT
        All day
        3:30 PM PDT
        3:30 PM PDT to 7:30 PM EDT

        1 day
        2 days
        1 day 1 hour
        1 day 4 hours 18 minutes
        """

        # Bind to '_' so pygettext.py will pick this up for translation
        _ = self.translation.ugettext

        tzStart = tzEnd = None
        dtStart = component.propertyNativeValue("DTSTART")
        if isinstance(dtStart, datetime.datetime):
            tzStart = dtStart.tzname()
        else:
            return ("", _("All day"))

        # tzStart = component.getProperty("DTSTART").params().get("TZID", "UTC")

        dtEnd = component.propertyNativeValue("DTEND")
        if dtEnd:
            if isinstance(dtEnd, datetime.datetime):
                tzEnd = dtEnd.tzname()
            # tzEnd = component.getProperty("DTEND").params().get("TZID", "UTC")
            duration = dtEnd - dtStart
        else:
            tzEnd = tzStart
            duration = component.propertyNativeValue("DURATION")
            if duration:
                dtEnd = dtStart + duration
            else:
                if isinstance(dtStart, datetime.date):
                    dtEnd = None
                    duration = datetime.timedelta(days=1)
                else:
                    dtEnd = dtStart + datetime.timedelta(days=1)
                    dtEnd.hour = dtEnd.minute = dtEnd.second = 0
                    duration = dtEnd - dtStart

        if dtStart == dtEnd:
            return (self.dtTime(dtStart), "")

        return (
            _("%(startTime)s to %(endTime)s")
            % {
                'startTime'      : self.dtTime(dtStart,
                                    includeTimezone=(tzStart != tzEnd)),
                'endTime'        : self.dtTime(dtEnd),
            },
            self.dtDuration(duration)
        )


    def dtDate(self, val):
        # Bind to '_' so pygettext.py will pick this up for translation
        _ = self.translation.ugettext

        return (
            _("%(dayName)s, %(monthName)s %(dayNumber)d, %(yearNumber)d")
            % {
                'dayName'    : _(daysFull[val.weekday()]),
                'monthName'  : _(monthsFull[val.month]),
                'dayNumber'  : val.day,
                'yearNumber' : val.year,
            }
        )

    def dtTime(self, val, includeTimezone=True):
        if not isinstance(val, (datetime.datetime, datetime.time)):
            return ""

        # Bind to '_' so pygettext.py will pick this up for translation
        _ = self.translation.ugettext

        ampm = _("AM") if val.hour < 12 else _("PM")
        hour12 = val.hour % 12
        if hour12 == 0:
            hour12 = 12

        result = (
            _("%(hour12Number)d:%(minuteNumber)02d %(ampm)s")
            % {
                'hour24Number' : val.hour, # 0-23
                'hour12Number' : hour12, # 1-12
                'minuteNumber' : val.minute, # 0-59
                'ampm'         : _(ampm),
            }
        )

        if includeTimezone and val.tzname():
            result += " %s" % (val.tzname())

        return result

    def dtDuration(self, val):

        # Bind to '_' so pygettext.py will pick this up for translation
        _ = self.translation.ugettext

        parts = []

        if val.days == 1:
            parts.append(_("1 day"))
        elif val.days > 1:
            parts.append(_("%(dayCount)d days" %
                { 'dayCount' : val.days }))

        hours = val.seconds / 3600
        minutes = divmod(val.seconds / 60, 60)[1]
        seconds = divmod(val.seconds, 60)[1]

        if hours == 1:
            parts.append(_("1 hour"))
        elif hours > 1:
            parts.append(_("%(hourCount)d hours") %
                { 'hourCount' : hours })

        if minutes == 1:
            parts.append(_("1 minute"))
        elif minutes > 1:
            parts.append(_("%(minuteCount)d minutes") %
                { 'minuteCount' : minutes })

        if seconds == 1:
            parts.append(_("1 second"))
        elif seconds > 1:
            parts.append(_("%(secondCount)d seconds") %
                { 'secondCount' : seconds })

        return " ".join(parts)


# The strings below are wrapped in _( ) for the benefit of pygettext.  We don't
# actually want them translated until they're used.

_ = lambda x: x

daysFull = [
    _("Monday"),
    _("Tuesday"),
    _("Wednesday"),
    _("Thursday"),
    _("Friday"),
    _("Saturday"),
    _("Sunday"),
]

daysAbbrev = [
    _("Mon"),
    _("Tue"),
    _("Wed"),
    _("Thu"),
    _("Fri"),
    _("Sun"),
    _("Sat"),
]

monthsFull = [
    "datetime.month is 1-based",
    _("January"),
    _("February"),
    _("March"),
    _("April"),
    _("May"),
    _("June"),
    _("July"),
    _("August"),
    _("September"),
    _("October"),
    _("November"),
    _("December"),
]

monthsAbbrev = [
    "datetime.month is 1-based",
    _("Jan"),
    _("Feb"),
    _("Mar"),
    _("Apr"),
    _("May"),
    _("Jun"),
    _("Jul"),
    _("Aug"),
    _("Sep"),
    _("Oct"),
    _("Nov"),
    _("Dec"),
]
