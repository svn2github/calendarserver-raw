##
# Copyright (c) 2005, 2010 Apple Computer, Inc. All rights reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
# DRI: Wilfredo Sanchez, wsanchez@apple.com
##

"""
WebDAV XML parsing.

This module provides XML utilities for use with WebDAV.

See RFC 2518: http://www.ietf.org/rfc/rfc2518.txt (WebDAV)
"""

__all__ = [
    "registerElement",
    "registerElements",
    "lookupElement",
    "WebDAVDocument",
]

import cStringIO as StringIO
import xml.dom.minidom
import xml.sax

import lxml.etree
from twext.web2.dav.element.base import WebDAVElement, WebDAVUnknownElement, PCDATAElement
from twext.web2.dav.element.util import PrintXML

##
# Parsing
##

def registerElements(module):
    """
    Register XML elements defined in the given module with the parser.
    """
    element_names = []

    for element_class_name in dir(module):
        element_class = getattr(module, element_class_name)

        if type(element_class) is type and issubclass(element_class, WebDAVElement):
            if element_class.namespace is None: continue
            if element_class.name is None: continue
            if element_class.unregistered: continue

            registerElement(element_class, element_names)

    return element_names

def registerElement(element_class, element_names=None):
    """
    Register the supplied XML elements with the parser.
    """
    qname = element_class.namespace, element_class.name
    if qname in elements_by_tag_name:
        raise AssertionError(
            "Attempting to register qname %s multiple times: (%r, %r)"
            % (qname, elements_by_tag_name[qname], element_class)
        )
    
    if not (qname in elements_by_tag_name and issubclass(element_class, elements_by_tag_name[qname])):
        elements_by_tag_name[qname] = element_class
        if element_names is not None:
            element_names.append(element_class.__name__)

def lookupElement(qname):
    """
    Return the element class for the element with the given qname.
    """
    return elements_by_tag_name[qname]

elements_by_tag_name = {}

class PyXMLWebDAVContentHandler (xml.sax.handler.ContentHandler):
    def setDocumentLocator(self, locator): self.locator = locator
    locator = None

    def location(self):
        return "line %d, column %d" % (self.locator.getLineNumber(), self.locator.getColumnNumber())

    def startDocument(self):
        self.stack = [{
            "name"       : None,
            "class"      : None,
            "attributes" : None,
            "children"   : [],
        }]

        # Keep a cache of the subclasses we create for unknown XML
        # elements, so that we don't create multiple classes for the
        # same element; it's fairly typical for elements to appear
        # multiple times in a document.
        self.unknownElementClasses = {}

    def endDocument(self):
        top = self.stack[-1]

        assert top["name"] is None
        assert top["class"] is None
        assert top["attributes"] is None
        assert len(top["children"]) is 1, "Must have exactly one root element, got %d" % len(top["children"])

        self.dom = WebDAVDocument(top["children"][0])
        del(self.unknownElementClasses)

    def startElementNS(self, name, qname, attributes):
        attributes_dict = {}

        if attributes.getLength() is not 0:
            for attr_name in attributes.getQNames():
                attributes_dict[attr_name.encode("utf-8")] = attributes.getValueByQName(attr_name)

        tag_namespace, tag_name = name
        if name in elements_by_tag_name:
            element_class = elements_by_tag_name[name]
        elif name in self.unknownElementClasses:
            element_class = self.unknownElementClasses[name]
        else:
            def element_class(*args, **kwargs):
                element = WebDAVUnknownElement(*args, **kwargs)
                element.namespace = tag_namespace.encode('utf-8') if tag_namespace else tag_namespace
                element.name      = tag_name.encode('utf-8')
                return element
            self.unknownElementClasses[name] = element_class

        self.stack.append({
            "name"       : name,
            "class"      : element_class,
            "attributes" : attributes_dict,
            "children"   : [],
        })

    def endElementNS(self, name, qname):
        # Pop the current element from the stack...
        top = self.stack[-1]
        del(self.stack[-1])

        assert top["name"] == name, "Last item on stack is %s while closing %s" % (top["name"], name)

        # ...then instantiate the element and add it to the parent's list of
        # children.
        try:
            element = top["class"](*top["children"], **top["attributes"])
        except ValueError, e:
            e.args = ("%s at %s" % (e.args[0], self.location()),) + e.args[1:]
            raise # Re-raises modified e, but preserves traceback

        self.stack[-1]["children"].append(element)

    def characters(self, content):
        # Coalesce adjacent PCDATAElements
        pcdata = PCDATAElement(content)
        if len(self.stack[-1]["children"]) and isinstance(self.stack[-1]["children"][-1], PCDATAElement):
            self.stack[-1]["children"][-1] = self.stack[-1]["children"][-1] + pcdata
        else:
            self.stack[-1]["children"].append(pcdata)

    def ignorableWhitespace(self, whitespace):
        self.characters(self, whitespace)

    def startElement(self, name, attributes):
        raise AssertionError("startElement() should not be called by namespace-aware parser")

    def endElement(self, name):
        raise AssertionError("endElement() should not be called by namespace-aware parser")

    def processingInstruction(self, target, data):
        raise AssertionError("processing instructions are not allowed")

    def skippedEntity(self, name):
        raise AssertionError("skipped entities are not allowed")

class pyxmlparser(object):
    def parseElement(self, source):
        raise NotImplementedError('PyXML parser does not have support for parsing from an arbitrary element')

    def parseString(self, source):
        return self.parse(StringIO.StringIO(source))

    def parse(self, source):
        handler = PyXMLWebDAVContentHandler()
        parser  = xml.sax.make_parser()

        parser.setContentHandler(handler)
        parser.setFeature(xml.sax.handler.feature_namespaces, True)

        try:
            parser.parse(source)
        except xml.sax.SAXParseException, e:
            raise ValueError(e)

        return handler.dom

    parseStream = parse


class WebDAVContentHandler(object):
    class StackData(object):
        def __init__(self, name, klass, attributes, children):
            self.name = name
            self.klass = klass
            self.attributes = attributes
            self.children = children

    def __init__(self):
        self.handlers = {'start' : self.startElement,
                         'end'   : self.endElement}

        self.stack = [self.StackData(None, None, None, [])]

        # Keep a cache of the subclasses we create for unknown XML
        # elements, so that we don't create multiple classes for the
        # same element; it's fairly typical for elements to appear
        # multiple times in a document.
        self.unknownElementClasses = {}

    def handle(self, event, element):
        handlerMethod = self.handlers.get(event, lambda *args: None)
        handlerMethod(element)

    def getQname(self, element):
        tag_namespace = element.nsmap[element.prefix]
        if tag_namespace:
            tag_name = element.tag.split("{%s}" % tag_namespace)[1]
        else:
            tag_name = element.tag
        return (tag_namespace, tag_name)

    def endDocument(self):
        top = self.stack[-1]

        assert top.name is None
        assert top.klass is None
        assert top.attributes is None
        assert len(top.children) is 1, "Must have exactly one root element, got %d" % len(top.children)

        self.dom = WebDAVDocument(top.children[0])
        del(self.unknownElementClasses)

    def startElement(self, element):
        name = self.getQname(element)
        if name in elements_by_tag_name:
            element_class = elements_by_tag_name[name]
        elif name in self.unknownElementClasses:
            element_class = self.unknownElementClasses[name]
        else:
            (tag_namespace, tag_name) = name

            def element_class(*args, **kwargs):
                element = WebDAVUnknownElement(*args, **kwargs)
                element.namespace = tag_namespace.encode('utf-8') if tag_namespace else tag_namespace
                element.name      = tag_name.encode('utf-8')
                return element

            self.unknownElementClasses[name] = element_class
        attributes = {}
        for k, v in element.items():
            #Cheat a little.
            k = k.replace('{http://www.w3.org/XML/1998/namespace}', 'xml:')
            attributes[k] = v

        stackData = self.StackData(name, element_class, attributes, [])
        self.stack.append(stackData)

    def endElement(self, element):
        # Pop the current element from the stack...
        top = self.stack[-1]
        del(self.stack[-1])

        name = self.getQname(element)
        assert top.name == name, "Last item on stack is %s while closing %s" % (top.name, name)

        if element.text:
            text = element.text.strip()
            if text:
                top.children.append(PCDATAElement(text))

        # ...then instantiate the element and add it to the parent's list of
        # children.
        try:
            davElement = top.klass(*top.children, **top.attributes)
        except ValueError, e:
            e.args = ("%s at %s" % (e.args[0], element.sourceline)) + e.args[1:]
            raise # Re-raises modified e, but preserves traceback
        
        self.stack[-1].children.append(davElement)
        element.clear()

class lxmlparser(object):
    def parseElement(self, source):
        return self.parse(source, lxml.etree.iterwalk)
    
    def parseString(self, source):
        return self.parse(StringIO.StringIO(source))

    def parse(self, source, iterate=lxml.etree.iterparse):
        handler = WebDAVContentHandler()
        try: 
            context = iterate(source, events=('start', 'end'))
            [handler.handle(event, element) for (event, element) in context]
            handler.endDocument()
        except lxml.etree.Error as e:
            raise ValueError(e)

        return handler.dom

    parseStream = parse

# Hook to override if needed
ParserClass = lxmlparser

class WebDAVDocument (object):
    """
    WebDAV XML document.
    """
    @classmethod
    def fromStream(cls, source):
        return ParserClass().parseStream(source)

    @classmethod
    def fromString(cls, source):
        return ParserClass().parseString(source)

    @classmethod
    def fromElement(cls, source):
        return ParserClass().parseElement(source)

    def __init__(self, root_element):
        """
        root_element must be a WebDAVElement instance.
        """
        super(WebDAVDocument, self).__init__()

        if not isinstance(root_element, WebDAVElement):
            raise ValueError("Not a WebDAVElement: %r" % (root_element,))

        self.root_element = root_element

    def __str__(self):
        output = StringIO.StringIO()
        self.writeXML(output)
        return output.getvalue()

    def __eq__(self, other):
        if isinstance(other, WebDAVDocument):
            return self.root_element == other.root_element
        else:
            return NotImplemented

    def writeXML(self, output):
        document = xml.dom.minidom.Document()
        self.root_element.addToDOM(document, None)
        PrintXML(document, stream=output)

    def toxml(self):
        output = StringIO.StringIO()
        self.writeXML(output)
        return output.getvalue()
