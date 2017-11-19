"""
Interface to CMS data management service PhEDEx.
"""

import logging
import pprint

from webservice import RESTService
from common.configuration import common_config

LOG = logging.getLogger(__name__)

class PhEDEx(object):
    def __init__(self, cache_lifetime = 0):
        self._rest = RESTService(common_config.phedex.url, use_cache = (cache_lifetime > 0))
        self.cache_lifetime = cache_lifetime

    def call(self, command, options = [], method = GET, format = 'url', raw_output = False):
        response = self._rest.make_request(command, options = options, method = method, format = format, cache_lifetime = self.cache_lifetime)

        try:
            result = response['phedex']
        except KeyError:
            LOG.error(response)
            return

        if LOG.getEffectiveLevel() == logging.DEBUG:
            LOG.debug(pprint.pformat(result))

        if raw_output:
            return result

        for metadata in ['request_timestamp', 'instance', 'request_url', 'request_version', 'request_call', 'call_time', 'request_date']:
            result.pop(metadata)

        # the only one item left in the results should be the result body. Clone the keys to use less memory..
        key = result.keys()[0]
        body = result[key]
        
        return body

    def form_catalog_xml(self, file_catalogs, human_readable = False):
        """
        Take a catalog dict of form {dataset: [block]} and form an input xml for delete and subscribe calls.
        @param file_catalogs   {dataset: [block]}
        @param human_readable  If True, return indented xml.
        @return  An xml document for delete and subscribe calls.
        """

        # we should consider using an actual xml tool
        xml = '<data version="2.0">{nl}'
        xml += '{i1}<dbs name="%s">{nl}' % config.dbs.url_base

        for dataset, blocks in file_catalogs.iteritems():
            xml += '{i2}<dataset name="%s" is-open="%s">{nl}' % (dataset.name, ('y' if dataset.is_open else 'n'))

            for block in blocks:
                xml += '{i3}<block name="%s" is-open="%s"/>{nl}' % (block.full_name(), ('y' if block.is_open else 'n'))

            xml += '{i2}</dataset>{nl}'

        xml += '{i1}</dbs>{nl}'
        xml += '</data>{nl}'

        if human_readable:
            return xml.format(nl = '\n', i1 = ' ', i2 = '  ', i3 = '   ')
        else:
            return xml.format(nl = '', i1 = '', i2 = '', i3 = '')
