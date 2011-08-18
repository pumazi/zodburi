import os
import cgi
from cStringIO import StringIO
import urlparse

from ZEO.ClientStorage import ClientStorage
from ZODB.FileStorage.FileStorage import FileStorage
from ZODB.DemoStorage import DemoStorage
from ZODB.MappingStorage import MappingStorage
from ZODB.blob import BlobStorage
from ZODB.DB import DB
import ZConfig

from zodburi.datatypes import convert_bytesize
from zodburi.datatypes import convert_int


class Resolver(object):
    _int_args = ()
    _string_args = ()
    _bytesize_args = ()

    def interpret_kwargs(self, kw):
        unused = kw.copy()
        new = {}
        convert_string = lambda s: s
        converters = (
            convert_int,
            convert_string,
            convert_bytesize)
        args = (
            self._int_args,
            self._string_args,
            self._bytesize_args)
        for convert, arg_names in zip(converters, args):
            for arg_name in arg_names:
                value = unused.pop(arg_name, None)
                if value is not None:
                    value = convert(value)
                    new[arg_name] = value

        return new, unused


class MappingStorageURIResolver(Resolver):

    def __call__(self, uri):
        prefix, rest = uri.split('memory://', 1)
        result = rest.split('?', 1)
        if len(result) == 1:
            name = result[0]
            query = ''
        else:
            name, query = result
        kw = dict(cgi.parse_qsl(query))
        kw, unused = self.interpret_kwargs(kw)
        args = (name,)
        def factory():
            return MappingStorage(*args)
        return factory, unused


class FileStorageURIResolver(Resolver):
    # XXX missing: blob_dir, packer, pack_keep_old, pack_gc, stop
    _int_args = ('create', 'read_only', 'demostorage')
    _string_args = ('blobstorage_dir', 'blobstorage_layout')
    _bytesize_args = ('quota',)

    def __call__(self, uri):
        # we can't use urlparse.urlsplit here due to Windows filenames
        prefix, rest = uri.split('file://', 1)
        result = rest.split('?', 1)
        if len(result) == 1:
            path = result[0]
            query = ''
        else:
            path, query = result
        path = os.path.normpath(path)
        args = (path,)
        kw = dict(cgi.parse_qsl(query))
        kw, unused = self.interpret_kwargs(kw)
        demostorage = False

        if 'demostorage'in kw:
            kw.pop('demostorage')
            demostorage = True

        blobstorage_dir = None
        blobstorage_layout = 'automatic'
        if 'blobstorage_dir' in kw:
            blobstorage_dir = kw.pop('blobstorage_dir')
        if 'blobstorage_layout' in kw:
            blobstorage_layout = kw.pop('blobstorage_layout')

        if demostorage and blobstorage_dir:
            def factory():
                filestorage = FileStorage(*args, **kw)
                blobstorage = BlobStorage(blobstorage_dir, filestorage,
                                          layout=blobstorage_layout)
                return DemoStorage(base=blobstorage)
        elif blobstorage_dir:
            def factory():
                filestorage = FileStorage(*args, **kw)
                return BlobStorage(blobstorage_dir, filestorage,
                                          layout=blobstorage_layout)
        elif demostorage:
            def factory():
                filestorage = FileStorage(*args, **kw)
                return DemoStorage(base=filestorage)
        else:
            def factory():
                return FileStorage(*args, **kw)

        return factory, unused


class ClientStorageURIResolver(Resolver):
    _int_args = ('debug', 'min_disconnect_poll', 'max_disconnect_poll',
                 'wait_for_server_on_startup', 'wait', 'wait_timeout',
                 'read_only', 'read_only_fallback', 'shared_blob_dir',
                 'demostorage')
    _string_args = ('storage', 'name', 'client', 'var', 'username',
                    'password', 'realm', 'blob_dir')
    _bytesize_args = ('cache_size', )

    def __call__(self, uri):
        # urlparse doesnt understand zeo URLs so force to something that doesn't break
        uri = uri.replace('zeo://', 'http://', 1)
        (scheme, netloc, path, query, frag) = urlparse.urlsplit(uri)
        if netloc:
            # TCP URL
            if ':' in netloc:
                host, port = netloc.split(':')
                port = int(port)
            else:
                host = netloc
                port = 9991
            args = ((host, port),)
        else:
            # Unix domain socket URL
            path = os.path.normpath(path)
            args = (path,)
        kw = dict(cgi.parse_qsl(query))
        kw, unused = self.interpret_kwargs(kw)
        if 'demostorage' in kw:
            kw.pop('demostorage')
            def factory():
                return DemoStorage(base=ClientStorage(*args, **kw))
        else:
            def factory():
                return ClientStorage(*args, **kw)
        return factory, unused

def get_dbkw(kw):
    dbkw = {}
    dbkw['cache_size'] = 10000
    dbkw['pool_size'] = 7
    dbkw['database_name'] = 'unnamed'
    if 'connection_cache_size' in kw:
        dbkw['cache_size'] = int(kw.pop('connection_cache_size'))
    if 'connection_pool_size' in kw:
        dbkw['pool_size'] = int(kw.pop('connection_pool_size'))
    if 'database_name' in kw:
        dbkw['database_name'] = kw.pop('database_name')

    return dbkw


class ZConfigURIResolver(object):

    schema_xml_template = """
    <schema>
        <import package="ZODB"/>
        <multisection type="ZODB.storage" attribute="storages" />
    </schema>
    """

    def __call__(self, uri):
        (scheme, netloc, path, query, frag) = urlparse.urlsplit(uri)
         # urlparse doesnt understand file URLs and stuffs everything into path
        (scheme, netloc, path, query, frag) = urlparse.urlsplit('http:' + path)
        path = os.path.normpath(path)
        schema_xml = self.schema_xml_template
        schema = ZConfig.loadSchemaFile(StringIO(schema_xml))
        config, handler = ZConfig.loadConfig(schema, path)
        for factory in config.storages:
            if not frag:
                # use the first defined in the file
                break
            elif frag == factory.name:
                # match found
                break
        else:
            raise KeyError("No storage named %s found" % frag)
        return factory.open, {}


RESOLVERS = {
    'zeo':ClientStorageURIResolver(),
    'file':FileStorageURIResolver(),
    'zconfig':ZConfigURIResolver(),
    'memory':MappingStorageURIResolver(),
    }