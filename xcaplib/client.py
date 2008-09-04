#!/usr/bin/env python
"urllib2-based XCAP client"

import urllib2
from urllib2 import HTTPError, URLError, addinfourl

# Q: where's asynchronous twisted-based client?
# A: Twisted implementation of http client in both twisted-web and twisted-web2
#    packages seems rather incomplete. So, it is easier to implement
#    a client using a nice blocking API, then wrap it in a worker thread and thus get
#    an unblocking one.

AGENT = 'xcapclient.py'

__all__ = ['Resource',
           'Document',
           'Element',
           'AttributeValue',
           'NSBindings',
           'XCAPClient',
           'HTTPError',
           'URLError',
           'addinfourl']

class Resource(str):
    """Result of XCAP GET request: document + etag"""

    def __new__(cls, source, _etag, _content_type=None):
        return str.__new__(cls, source)

    def __init__(self, _source, etag, content_type=None):
        self.etag = etag
        if content_type is not None:
            self.content_type = content_type

    @staticmethod
    def get_class(content_type):
        "For given content-type, return an appropriate subclass of Resource"
        if content_type == Element.content_type:
            return Element
        elif content_type == AttributeValue.content_type:
            return AttributeValue
        elif content_type == NSBindings.content_type:
            return NSBindings
        else:
            return lambda source, etag: Document(source, etag, content_type)

    @staticmethod
    def get_content_type(node):
        "For given node selector, return an appropriate content-type for PUT request"
        if node is None:
            return None
        elif node.endswith('namespace::*'):
            return NSBindings.content_type
        elif node[node.rindex('/'):][:1] == '@':
            return AttributeValue.content_type
        else:
            return Element.content_type

    def __eq__(self, other):
        try:
            return str.__eq__(self, other) and self.etag==other.etag and self.content_type==other.content_type
        except AttributeError:
            return True

    def __ne__(self, other):
        try:
            return str.__ne__(self, other) or self.etag!=other.etag or self.content_type!=other.content_type
        except AttributeError:
            return False

class Document(Resource):
    content_type = None # depends on the application

class Element(Resource):
    content_type = 'application/xcap-el+xml'

class AttributeValue(Resource):
    content_type = 'application/xcap-att+xml'

class NSBindings(Resource):
    content_type = 'application/xcap-ns+xml'


class HTTPRequest(urllib2.Request):
    """Hack urllib2.Request to support PUT and DELETE methods."""
    
    def __init__(self, url, method="GET", data=None, headers={},
                 origin_req_host=None, unverifiable=False):
        urllib2.Request.__init__(self,url,data,headers,origin_req_host,unverifiable)
        self.url = url
        self.method = method
    
    def get_method(self):
        return self.method

def parse_etag_value(s):
    if s is None:
        return s
    if len(s)>1 and s[0]=='"' and s[-1]=='"':
        return s[1:-1]
    else:
        raise ValueError('Cannot parse etag header value: %r' % s)


# XCAPClient uses HTTPConnectionWrapper-like class for HTTP handling.
# if HTTPConnectionWrapper blocks, XCAPClient should blocks,
# if it's not (returning Deferred), XCAPClient is async as well
# This means XCAPClient doesn't look into results of HTTP resuests.
class HTTPConnectionWrapper(object):

    def __init__(self, base_url, user, password=None, auth=None):
        self.base_url = base_url
        if self.base_url[-1:]!='/':
            self.base_url += '/'

        self.username, self.domain = user.split('@')
        self.password = password

        handlers = []

        def add_handler(klass):
            handler = klass()
            handler.add_password(self.domain, self.base_url, self.username, password)
            handlers.append(handler)
        
        if auth == 'basic':
            add_handler(urllib2.HTTPBasicAuthHandler)
        elif auth == "digest":
            add_handler(urllib2.HTTPDigestAuthHandler)
        elif password is not None:
            add_handler(urllib2.HTTPDigestAuthHandler)
            add_handler(urllib2.HTTPBasicAuthHandler)
        self.opener = urllib2.build_opener(*handlers)

    def request(self, method, path, headers=None, data=None, etag=None):
        if path[:1]=='/':
            path = path[1:]
        if headers==None:
            headers = {}
        if etag is not None:
            headers['If-Match'] = '"' + etag + '"'
        url = self.base_url+path
        req = HTTPRequest(url, method=method, headers=headers, data=data)
        try:
            response = self.opener.open(req)
            response.etag = parse_etag_value(response.headers.get('etag'))
            return response
            # contrary to what documentation for urllib2 says, this can return addinfourl
            # instead of HTTPError which is though has all the relevant attributes (code, msg etc)
        except HTTPError, e:
            e.etag = parse_etag_value(e.headers.get('etag'))
            if 200 <= e.code <= 299:
                return e
            raise

    def get(self, path, headers=None, etag=None):
        response = self.request('GET', path, headers, None, etag)
        if 200 <= response.code <= 299:
            content_type = response.headers.get('content-type')
            klass = Resource.get_class(content_type)
            return klass(response.read(), response.etag)
        else:
            raise response

class Error(Exception):
    pass

class AlreadyExists(Error):
    pass

class XCAPClient(object):

    HTTPConnectionWrapper = HTTPConnectionWrapper

    def __init__(self, root, user, password=None, auth='basic', connection=None):
        self.root = root
        if self.root[-1:] == '/':
            self.root = self.root[:-1]
        if user[:-4] == 'sip:':
            user = user[4:]
        self.user = user
        if connection is None:
            self.con = self.HTTPConnectionWrapper(self.root, user, password, auth)
        else:
            self.con = connection

    def get_path(self, application, node):
        path = "/%s/users/%s/index.xml" % (application, self.user)
        if node:
            path += '~~' + node
        return path

    def get_url(self, application, node):
        return (self.root or '') + self.get_path(application, node)

    def get(self, application, node=None, etag=None):
        path = self.get_path(application, node)
        return self.con.get(path, etag=etag)

    def put(self, application, resource, node=None, etag=None):
        path = self.get_path(application, node)
        headers = {}
        content_type = Resource.get_content_type(node)
        if content_type:
            headers['Content-Type'] = content_type
        return self.con.request('PUT', path, headers, resource, etag=etag)

    def delete(self, application, node=None, etag=None):
        path = self.get_path(application, node)
        return self.con.request('DELETE', path, etag=etag)

    def replace(self, application, resource, node=None, etag=None):
        """check that the already exists. if so, PUT.
        Return (old_resource, reply to PUT)
        """
        old = self.get(application, node, etag)
        res = self.put(application, resource, node, old.etag)
        return (old, res)

    def insert_document(self, application, resource):
        """check that the resource doesn't exists. if so, PUT.

        Since 404 doesn't return ETag, it is not reliable (someone could
        do PUT after our GET and we will replace the document, instead of inserting.
        """
        try:
            self.get(application)
        except HTTPError, ex:
            if ex.code == 404:
                # how to ensure insert?
                # 1. make openxcap to supply fixed tag into 404, like ETag: "none"
                # and understand If-Match: "none" as intent to insert.
                # 2. If-None-Match: *, what does it do?
                return self.put(application, resource)
        else:
            raise AlreadyExists

    def insert(self, application, resource, node=None, etag=None, retries=5):
        """check that the resource doesn't exists. if so, PUT.
        1. Get the whole document. This is needed for etag.
        2. If node supplied, check that that node doesn't exists (it
           could be done locally, but we're doing it via another GET to
           the server)
        3. PUT the resource.
        """
        if node is None:
            if etag is not None:
                raise ValueError('Cannot PUT the document, reliably. Set etag to None')
            return self.insert_document(application, resource)

        while retries>=0:
            retries -= 1
            document = self.get(application, None, etag)
            try:
                element = self.get(application, node, document.etag)
            except HTTPError, ex:
                if etag is None and ex.code == 412:
                    continue
                elif ex.code == 404:
                    try:
                        return self.put(application, resource, node, document.etag)
                    except HTTPError, ex:
                        if etag is None and ex.code == 412:
                            continue
                        else:
                            raise
                else:
                    raise
        else:
            raise AlreadyExists


if __name__ == '__main__':

    root = 'http://127.0.0.1:8000'
    user = 'alice@example.com'
    client = XCAPClient(root, user, password='123')

    document = file('resource-lists.xml').read()

    # put the whole document
    client.put('resource-lists', document)

    # get the whole document
    got = client.get('resource-lists')

    # it must be the same
    assert document==got, (document, got)

    # get an element:
    res = client.get('resource-lists', '/resource-lists/list/entry/display-name')
    assert res == '<display-name>Bill Doe</display-name>', res

    # get an attribute:
    res = client.get('resource-lists', '/resource-lists/list/entry/@uri')
    assert res == 'sip:bill@example.com', res

    # element operations:
    bob_uri = 'sip:bob@example.com'
    node_selector = '/resource-lists/list/entry[@uri="%s"]' % bob_uri

    # replace an element (when there isn't one)
    bob1 = '<entry uri="%s"><display-name>The Bob</display-name></entry>' % bob_uri
    try:
        res = client.replace('resource-lists', bob1, node_selector)
        assert False, 'should not get there'
    except HTTPError, e:
        if e.code != 404:
            raise

    # insert an element
    bob2 = '<entry uri="%s"/>' % bob_uri
    res = client.insert('resource-lists', bob2, node_selector, etag=res.etag)
    assert res.code == 201, (res.code, res)

    # insert an element (when there's already one)
    try:
        res = client.insert('resource-lists', bob2, node_selector)
        assert False, `res`
    except AlreadyExists:
        pass

    # replace an element, check etag by the way, it should be equal to that of last result
    res = client.put('resource-lists', bob1, node_selector, etag=res.etag)
    assert res.code == 200, (res.code, res)

    # delete an element
    res = client.delete('resource-lists', node_selector, etag=res.etag)
    assert res.code == 200, (res.code, res)

    # common http errors:
    try:
        res = client.delete('resource-lists', node_selector)
        assert res.code == 200, (res.code, res)
    except HTTPError, e:
        if e.code != 404:
            raise

    # connection errors:
    client2 = XCAPClient('http://www.fdsdfgh.com:32452', user)
    try:
        client2.get('resource-lists')
        assert False, 'should not get there'
    except URLError:
        pass

    # https and authentication:
    root = 'https://xcap.sipthor.net/xcap-root'
    client3 = XCAPClient(root, 'poc@umts.ro', 'poc', auth='basic')
    watchers = client3.get('watchers')
    assert isinstance(watchers, Document), `watchers`
    assert watchers.content_type == 'application/xml', watchers.content_type


    # conditional GET:
    client.put('resource-lists', document)
    got = client.get('resource-lists')    
    assert got==document, (document, got)
    etag = got.etag
    
    got2 = client.get('resource-lists', etag=etag)
    assert document==got2, (document, got2)

    try:
        got3 = client.get('resource-lists', etag=etag + 'xxx')
        assert False, "should've gotten 412 error instead: %r" % got3
    except HTTPError, e:
        if e.code != 412:
            raise

    # conditional DELETE:
    try:
        res = client.delete('resource-lists', etag=etag+'yyy')
        assert False, "should've gotten 412 error instead: %r" % res
    except HTTPError, e:
        if e.code != 412:
            raise