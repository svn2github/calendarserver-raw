# -*- test-case-name: calendarserver.webadmin.test.test_landing -*-
##
# Copyright (c) 2009-2014 Apple Inc. All rights reserved.
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
Calendar Server Configuration UI.
"""

__all__ = [
    "ConfigurationResource",
]

from twisted.web.template import renderer, tags as html

from .resource import PageElement, TemplateResource



class ConfigurationPageElement(PageElement):
    """
    Configuration management page element.
    """

    def __init__(self, configuration):
        PageElement.__init__(self, u"config")

        self.configuration = configuration


    def pageSlots(self):
        slots = {
            u"title": u"Server Configuration",
        }

        for key in (
            "ServerHostName",
            "HTTPPort",
            "SSLPort",
            "BindAddresses",
            "BindHTTPPorts",
            "BindSSLPorts",
            "EnableSSL",
            "RedirectHTTPToHTTPS",
            "SSLCertificate",
            "SSLPrivateKey",
            "SSLAuthorityChain",
            "SSLMethod",
            "SSLCiphers",
            "EnableCalDAV",
            "EnableCardDAV",
            "ServerRoot",
            "EnableSSL",
            "UserQuota",
            "MaxCollectionsPerHome",
            "MaxResourcesPerCollection",
            "MaxResourceSize",
            "UserName",
            "GroupName",
            "ProcessType",
            "MultiProcess.MinProcessCount",
            "MultiProcess.ProcessCount",
            "MaxRequests",
        ):
            value = self.configuration

            for key in key.split("."):
                value = getattr(value, key)

            def describe(value):
                if value == u"":
                    return u"(empty string)"

                if value is None:
                    return u"(no value)"

                return html.code(unicode(value))

            if isinstance(value, list):
                result = []
                for item in value:
                    result.append(describe(item))
                    result.append(html.br())
                result.pop()

            else:
                result = describe(value)

            slots[key] = result

        return slots


    @renderer
    def log_level_row(self, request, tag):
        def rowsForNamespaces(namespaces):
            for namespace in namespaces:
                yield tag.clone().fillSlots(
                    log_level_name="** name **",
                    log_level_value="** value **",
                )

        namespaces = ()

        return rowsForNamespaces(namespaces)



class ConfigurationResource(TemplateResource):
    """
    Configuration management page resource.
    """

    addSlash = False


    def __init__(self, configuration):
        TemplateResource.__init__(
            self, lambda: ConfigurationPageElement(configuration)
        )
