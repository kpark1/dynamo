import time
import logging

from dynamo.request.common import RequestManager
from dynamo.utils.interface.mysql import MySQL
from dynamo.dataformat.request import Request, RequestAction, DeletionRequest

LOG = logging.getLogger(__name__)

class DeletionRequestManager(RequestManager):
    def __init__(self, config = None):
        RequestManager.__init__(self, 'deletion', config)

    def lock(self): #override
        # Caller of this function is responsible for unlocking
        # Non-aliased locks are for insert & update statements later
        tables = [
            ('deletion_requests', 'r'), 'deletion_requests', ('active_deletions', 'a'), 'active_deletions',
            ('deletion_request_items', 'i'), 'deletion_request_items', ('deletion_request_sites', 's'), 'deletion_request_sites'
        ]

        if not self._read_only:
            self.registry.db.lock_tables(write = tables)

    def get_requests(self, request_id = None, statuses = None, users = None, items = None, sites = None):
        all_requests = {}

        sql = 'SELECT r.`id`, 0+r.`status`, UNIX_TIMESTAMP(r.`request_time`),'
        sql += ' r.`user`, r.`dn`, a.`item`, a.`site`, 0+a.`status`, UNIX_TIMESTAMP(a.`updated`)'
        sql += ' FROM `deletion_requests` AS r'
        sql += ' LEFT JOIN `active_deletions` AS a ON a.`request_id` = r.`id`'
        sql += self._make_registry_constraints(request_id, statuses, users, items, sites)
        sql += ' ORDER BY r.`id`'

        _rid = 0
        for rid, status, request_time, user, dn, a_item, a_site, a_status, a_update in self.registry.db.xquery(sql):
            if rid != _rid:
                _rid = rid
                request = all_requests[rid] = DeletionRequest(rid, user, dn, int(status), request_time)

            if a_item is not None:
                if request.actions is None:
                    request.actions = []

                request.actions.append(RequestAction(a_item, a_site, int(a_status), a_update))

        if len(all_requests) != 0:
            # get the sites
            sql = 'SELECT s.`request_id`, s.`site` FROM `deletion_request_sites` AS s WHERE s.`request_id` IN (%s)' %  ','.join('%d' % d for d in all_requests.iterkeys())
            for rid, site in self.registry.db.xquery(sql):
                all_requests[rid].sites.append(site)
    
            # get the items
            sql = 'SELECT i.`request_id`, i.`item` FROM `deletion_request_items` AS i WHERE i.`request_id` IN (%s)' %  ','.join('%d' % d for d in all_requests.iterkeys())
            for rid, item in self.registry.db.xquery(sql):
                all_requests[rid].items.append(item)

        if items is not None or sites is not None:
            self.registry.db.drop_tmp_table('ids_tmp')

        if (request_id is not None and len(all_requests) != 0) or \
           (statuses is not None and (set(statuses) < set(['new', 'activated']) or set(statuses) < set([Request.ST_NEW, Request.ST_ACTIVATED]))):
            # there's nothing in the archive
            return all_requests

        # Pick up archived requests from the history DB
        archived_requests = {}

        sql = 'SELECT r.`id`, 0+r.`status`, UNIX_TIMESTAMP(r.`request_time`),'
        sql += ' r.`rejection_reason`, u.`name`, u.`dn`'
        sql += ' FROM `deletion_requests` AS r'
        sql += ' INNER JOIN `users` AS u ON u.`id` = r.`user_id`'
        sql += self._make_history_constraints(request_id, statuses, users, items, sites)
        sql += ' ORDER BY r.`id`'

        for rid, status, request_time, reason, user, dn in self.history.db.xquery(sql):
            if rid not in all_requests:
                archived_requests[rid] = DeletionRequest(rid, user, dn, int(status), request_time, reason)

        if len(archived_requests) != 0:
            # get the sites
            sql = 'SELECT s.`request_id`, h.`name` FROM `deletion_request_sites` AS s'
            sql += ' INNER JOIN `sites` AS h ON h.`id` = s.`site_id`'
            sql += ' WHERE s.`request_id` IN (%s)' %  ','.join('%d' % d for d in archived_requests.iterkeys())
            for rid, site in self.history.db.xquery(sql):
                archived_requests[rid].sites.append(site)
    
            # get the datasets
            sql = 'SELECT i.`request_id`, d.`name` FROM `deletion_request_datasets` AS i'
            sql += ' INNER JOIN `datasets` AS d ON d.`id` = i.`dataset_id`'
            sql += ' WHERE i.`request_id` IN (%s)' %  ','.join('%d' % d for d in archived_requests.iterkeys())
            for rid, dataset in self.history.db.xquery(sql):
                archived_requests[rid].items.append(dataset)

            # get the blocks
            sql = 'SELECT i.`request_id`, d.`name`, b.`name` FROM `deletion_request_blocks` AS i'
            sql += ' INNER JOIN `blocks` AS b ON b.`id` = i.`block_id`'
            sql += ' INNER JOIN `datasets` AS d ON d.`id` = b.`dataset_id`'
            sql += ' WHERE i.`request_id` IN (%s)' %  ','.join('%d' % d for d in archived_requests.iterkeys())
            for rid, dataset, block in self.history.db.xquery(sql):
                archived_requests[rid].items.append(df.Block.to_full_name(dataset, block))

        all_requests.update(archived_requests)

        if items is not None or sites is not None:
            self.history.db.drop_tmp_table('ids_tmp')

        return all_requests

    def create_request(self, caller, items, sites):
        now = int(time.time())

        if self._read_only:
            return DeletionRequest(0, caller.name, caller.dn, 'new', now, None)

        # Make an entry in registry
        columns = ('user', 'dn', 'request_time')
        values = (caller.name, caller.dn, MySQL.bare('FROM_UNIXTIME(%d)' % now))
        request_id = self.registry.db.insert_get_id('deletion_requests', columns, values)

        mapping = lambda site: (request_id, site)
        self.registry.db.insert_many('deletion_request_sites', ('request_id', 'site'), mapping, sites)
        mapping = lambda item: (request_id, item)
        self.registry.db.insert_many('deletion_request_items', ('request_id', 'item'), mapping, items)

        # Make an entry in history
        history_user_ids = self.history.save_users([(caller.name, caller.dn)], get_ids = True)
        history_site_ids = self.history.save_sites(sites, get_ids = True)
        history_dataset_ids, history_block_ids = self._save_items(items)

        sql = 'INSERT INTO `deletion_requests` (`id`, `user_id`, `request_time`)'
        sql += ' SELECT %s, u.`id`, FROM_UNIXTIME(%s) FROM `groups` AS g, `users` AS u'
        sql += ' WHERE u.`dn` = %s'
        self.history.db.query(sql, request_id, now, caller.dn)

        mapping = lambda sid: (request_id, sid)
        self.history.db.insert_many('deletion_request_sites', ('request_id', 'site_id'), mapping, history_site_ids)
        mapping = lambda did: (request_id, did)
        self.history.db.insert_many('deletion_request_datasets', ('request_id', 'dataset_id'), mapping, history_dataset_ids)
        mapping = lambda bid: (request_id, bid)
        self.history.db.insert_many('deletion_request_blocks', ('request_id', 'block_id'), mapping, history_block_ids)

        return self.get_requests(request_id = request_id)[request_id]

    def update_request(self, request):
        if self._read_only:
            return

        sql = 'UPDATE `deletion_requests` SET `status` = %s, `rejection_reason` = %s WHERE `id` = %s'
        self.history.db.query(sql, request.status, request.reject_reason, request.request_id)

        if request.status in (Request.ST_NEW, Request.ST_ACTIVATED):
            sql = 'UPDATE `deletion_requests` SET `status` = %s, `request_time` = FROM_UNIXTIME(%s) WHERE `id` = %s'
            self.registry.db.query(sql, request.status, request.request_time, request.request_id)

            if request.actions is not None:
                # insert or update active copies
                fields = ('request_id', 'item', 'site', 'status', 'created', 'updated')
                update_columns = ('status', 'updated')
                for a in request.actions:
                    now = time.strftime('%Y-%m-%d %H:%M:%S') # current local time
                    values = (request.request_id, a.item, a.site, a.status, now, now)
                    self.registry.db.insert_update('active_deletions', fields, *values, update_columns = update_columns)

        else:
            # terminal state
            sql = 'DELETE FROM r, a, i, s USING `deletion_requests` AS r'
            sql += ' LEFT JOIN `active_deletions` AS a ON a.`request_id` = r.`id`'
            sql += ' LEFT JOIN `deletion_request_items` AS i ON i.`request_id` = r.`id`'
            sql += ' LEFT JOIN `deletion_request_sites` AS s ON s.`request_id` = r.`id`'
            sql += ' WHERE r.`id` = %s'
            self.registry.db.query(sql, request.request_id)

    def collect_updates(self, inventory):
        """
        Check active requests against the inventory state and set the status flags accordingly.
        """

        now = int(time.time())

        self.lock()

        try:
            active_requests = self.get_requests(statuses = [Request.ST_ACTIVATED])

            for request in active_requests.itervalues():
                if request.actions is None:
                    LOG.error('No active deletions for activated request %d', request.request_id)
                    request.status = Request.ST_COMPLETED
                    self.update_request(request)

                    continue

                updated = False

                for action in request.actions:
                    if action.status != RequestAction.ST_QUEUED:
                        continue

                    try:
                        site = inventory.sites[action.site]
                    except KeyError:
                        LOG.error('Unknown site %s', action.site)
                        action.status = RequestAction.ST_FAILED
                        action.last_update = now
                        updated = True

                        continue
               
                    try:
                        dataset_name, block_name = df.Block.from_full_name(action.item)
                    except df.ObjectError:
                        dataset_name = action.item
                        block_name = None
                    else:
                        pass
                        
                    try:
                        dataset = inventory.datasets[dataset_name]
                    except KeyError:
                        LOG.error('Unknown dataset %s', dataset_name)
                        action.status = RequestAction.ST_FAILED
                        action.last_update = now
                        updated = True

                        continue

                    if block_name is None:
                        # looking for a dataset replica

                        replica = site.find_dataset_replica(dataset)
                        if replica is None:
                            LOG.debug('Replica %s:%s gone', site.name, dataset.name)
                            action.status = RequestAction.ST_COMPLETED
                            action.last_update = now
                            updated = True

                    else:
                        block = dataset.find_block(block_name)
                        if block is None:
                            LOG.error('Unknown block %s', action.item)
                            action.status = RequestAction.ST_FAILED
                            action.last_update = now
                            updated = True
                            
                            continue
                
                        replica = site.find_block_replica(block)
                        if replica is None:
                            LOG.debug('Replica %s:%s gone', site.name, block.full_name())
                            action.status = RequestAction.ST_COMPLETED
                            action.last_update = now
                            updated = True

                n_complete = sum(1 for a in request.actions if a.status in (RequestAction.ST_COMPLETED, RequestAction.ST_FAILED))
                if n_complete == len(request.actions):
                    request.status = Request.ST_COMPLETED
                    updated = True

                if updated:
                    self.update_request(request)
                        
        finally:
            self.unlock()
