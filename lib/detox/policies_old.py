import time
import datetime
import re
import fnmatch

import detox.policy as policy
import detox.configuration as detox_config
import common.configuration as config
from common.dataformat import Site

class ProtectIncomplete(policy.ProtectPolicy):
    """
    PROTECT if the replica is not complete.
    """
    
    def __init__(self, name = 'ProtectIncomplete'):
        super(self.__class__, self).__init__(name, static = True)

    def applies(self, replica, demand_manager): # override
        return not replica.is_complete, 'Replica is not complete.'


class ProtectLocked(policy.ProtectPolicy):
    """
    PROTECT if any block of the dataset is locked.
    """

    def __init__(self, name = 'ProtectLocked'):
        super(self.__class__, self).__init__(name, static = True)

    def applies(self, replica, demand_manager): # override
        all_blocks = set([b.real_name() for b in replica.dataset.blocks])
        locked_blocks = set(demand_manager.dataset_demands[replica.dataset].locked_blocks)

        intersection = all_blocks & locked_blocks
        reason = 'Blocks locked: ' + ' '.join(intersection)
        
        return len(intersection) != 0, reason


class ProtectCustodial(policy.ProtectPolicy):
    """
    PROTECT if the replica is custodial.
    """

    def __init__(self, name = 'ProtectCustodial'):
        super(self.__class__, self).__init__(name, static = True)

    def applies(self, replica, demand_manager): # override
        return replica.is_custodial, 'Replica is custodial.'


class ProtectDiskOnly(policy.ProtectPolicy):
    """
    PROTECT if the dataset is not on tape. 
    """

    def __init__(self, name = 'ProtectDiskOnly'):
        super(self.__class__, self).__init__(name, static = True)

    def applies(self, replica, demand_manager): # override
        return not replica.dataset.on_tape, 'Dataset has no tape copy.'


class ProtectNonReadySite(policy.ProtectPolicy):
    """
    PROTECT if the site is not ready.
    """

    def __init__(self, name = 'ProtectNonReadySite'):
        super(self.__class__, self).__init__(name, static = True)

    def applies(self, replica, demand_manager): # override
        return replica.site.status != Site.STAT_READY, 'Site is not in ready state.'


class ProtectNotOwnedBy(policy.ProtectPolicy):
    """
    PROTECT if the replica is not fully owned by a group.
    """
    
    def __init__(self, group_name, name = 'ProtectNotOnwedBy'):
        super(self.__class__, self).__init__(name, static = True)
        self.group_name = group_name

    def applies(self, replica, demand_manager): # override
        return replica.group is None or replica.group.name != self.group_name, 'Not all parts of replica is owned by ' + self.group_name


class KeepTargetOccupancy(policy.KeepPolicy):
    """
    KEEP if occupancy of the replica's site is less than a set target.
    """

    def __init__(self, threshold, groups = [], name = 'ProtectTargetOccupancy'):
        super(self.__class__, self).__init__(name, static = False)

        self.threshold = threshold
        self.groups = groups

    def applies(self, replica, demand_manager): # override
        return replica.site.storage_occupancy(self.groups) < self.threshold, 'Site is underused.'


class DeletePartial(policy.DeletePolicy):
    """
    DELETE if the replica is partial.
    """

    def __init__(self, name = 'DeletePartial'):
        super(self.__class__, self).__init__(name, static = True)

    def applies(self, replica, demand_manager): # override
        return replica.is_partial, 'Replica is partial.'


class DeleteOld(policy.DeletePolicy):
    """
    DELETE if the replica is not accessed for more than a set time.
    """

    def __init__(self, threshold, unit, name = 'DeleteOld'):
        super(self.__class__, self).__init__(name, static = True)

        self.threshold_text = '%.1f%s' % (threshold, unit)

        if unit == 'y':
            threshold *= 365.
        if unit == 'y' or unit == 'd':
            threshold *= 24.
        if unit == 'y' or unit == 'd' or unit == 'h':
            threshold *= 3600.

        cutoff_timestamp = time.time() - threshold
        cutoff_datetime = datetime.datetime.utcfromtimestamp(cutoff_timestamp)
        self.cutoff = cutoff_datetime.date()

    def applies(self, replica, demand_manager): # override
        # the dataset was updated after the cutoff -> don't delete
        if datetime.datetime.utcfromtimestamp(replica.dataset.last_update).date() > self.cutoff:
            return False, ''

        # no accesses recorded ever -> delete
        if len(replica.accesses) == 0:
            return True, 'No access recorded for the replica.'

        for acc_type, records in replica.accesses.items(): # remote and local
            if len(records) == 0:
                continue

            last_acc_date = max(records.keys()) # datetime.date object set to UTC

            if last_acc_date > self.cutoff:
                return False, ''
            
        return True, 'Last access is older than ' + self.threshold_text + '.'


class DeleteUnpopular(policy.DeletePolicy):
    """
    DELETE if this is less popular than a threshold or is the least popular dataset at the site.
    """

    def __init__(self, name = 'DeleteUnpopular'):
        super(self.__class__, self).__init__(name, static = False)

        self.threshold = detox_config.delete_unpopular.threshold

    def applies(self, replica, demand_manager): # override
        score = demand_manager.dataset_demands[replica.dataset].request_weight
        if score == 0.:
            return True, 'Dataset has 0 request weight.'

        if score <= min(demand_manager.dataset_demands[r.dataset].request_weight for r in replica.site.dataset_replicas):
            return True, 'Dataset is the least popular at site.'
        else:
            return False, ''


class RecentMinimumCopies(policy.Policy):
    """
    PROTECT if the dataset has only minimum number of replicas, but DELETE if it is older than the threshold.
    """

    def __init__(self, threshold, unit, name = 'RecentMinimumCopies'):
        super(self.__class__, self).__init__(name, static = True)

        self.threshold_text = '%.1f%s' % (threshold, unit)

        if unit == 'y':
            threshold *= 365.
        if unit == 'y' or unit == 'd':
            threshold *= 24.
        if unit == 'y' or unit == 'd' or unit == 'h':
            threshold *= 3600.

        cutoff_timestamp = time.time() - threshold
        cutoff_datetime = datetime.datetime.utcfromtimestamp(cutoff_timestamp)
        self.cutoff = cutoff_datetime.date()

        self.action = policy.DEC_NEUTRAL # case_match is always called immediately after applies

    def applies(self, replica, demand_manager): # override
        self.action = policy.DEC_NEUTRAL

        if datetime.datetime.utcfromtimestamp(replica.last_block_created).date() < self.cutoff:
            # replica is old
            last_acc_date = datetime.date.min

            for acc_type, records in replica.accesses.items(): # remote and local
                if len(records) == 0:
                    continue
                
                last_acc_date = max([last_acc_date] + records.keys())

            if last_acc_date < self.cutoff:
                self.action = policy.DEC_DELETE
                return True, 'Replica is older than ' + self.threshold_text + '.'

        required_copies = demand_manager.dataset_demands[replica.dataset].required_copies
        if len(replica.dataset.replicas) <= required_copies:
            self.action = policy.DEC_PROTECT
            return True, 'Dataset has <= ' + str(required_copies) + ' copies.'

        return False, ''

    def case_match(self, replica): # override
        return self.action


class ActionList(policy.Policy):
    """
    Take decision from a list of policies.
    The list should have a decision, a site, and a dataset (wildcard allowed for both) per row, separated by white spaces.
    Any line that does not match the pattern
      (Keep|Delete) <site> <dataset>
    is ignored.
    """

    def __init__(self, list_path = '', name = 'ActionList'):
        super(self.__class__, self).__init__(name, static = True)

        self.res = [] # (action, site_re, dataset_re)
        self.patterns = [] # (action_str, site_pattern, dataset_pattern)
        self.action = policy.DEC_NEUTRAL

        if list_path:
            self.load_list(list_path)

    def add_action(self, action_str, site_pattern, dataset_pattern):
        site_re = re.compile(fnmatch.translate(site_pattern))
        dataset_re = re.compile(fnmatch.translate(dataset_pattern))

        if action_str == 'Keep':
            action = policy.DEC_PROTECT
        else:
            action = policy.DEC_DELETE

        self.res.append((action, site_re, dataset_re))
        self.patterns.append((action_str, site_pattern, dataset_pattern))

    def load_list(self, list_path):
        with open(list_path) as deletion_list:
            for line in deletion_list:
                matches = re.match('\s*(Keep|Delete)\s+([A-Za-z0-9_*]+)\s+(/[\w*-]+/[\w*-]+/[\w*-]+)', line.strip())
                if not matches:
                    continue

                action_str = matches.group(1)
                site_pattern = matches.group(2)
                dataset_pattern = matches.group(3)

                self.add_action(action_str, site_pattern, dataset_pattern)

    def load_lists(self, list_paths):
        for list_path in list_paths:
            self.load_list(list_path)

    def applies(self, replica, demand_manager): # override
        """
        Loop over the patterns list and make an entry in self.actions if the pattern matches.
        """

        self.action = policy.DEC_NEUTRAL

        matches = []
        for iL, (action, site_re, dataset_re) in enumerate(self.res):
            if site_re.match(replica.site.name) and dataset_re.match(replica.dataset.name):
                self.action = action
                matches.append(self.patterns[iL])

        if len(matches) != 0:
            return True, 'Pattern match: (action, site, dataset) = [%s]' % (','.join(['(%s, %s, %s)' % match for match in matches]))
        else:
            return False, ''
    
    def case_match(self, replica): # override
        return self.action


def make_stack(strategy):
    # return a *function* that returns the selected stack

    if strategy == 'TargetFraction':
        # stackgen(0.92) -> TargetFraction stack with threshold 92%
        def stackgen(*arg, **kwd):
            stack = [
                KeepTargetOccupancy(config.target_site_occupancy),
                ProtectNonReadySite(),
                ProtectIncomplete(),
                ProtectDiskOnly(),
                RecentMinimumCopies(*detox_config.delete_old.threshold),
                DeletePartial()
    #            DeleteUnpopular()
            ]

            if len(arg) != 0:
                stack[0].threshold = arg[0]

            if 'inventory' in kwd:
                stack[1].groups = [kwd['inventory'].groups['AnalysisOps']]
            
            return stack

    elif strategy == 'List':
        # stackgen([files]) -> List stack with files loaded into ActionList
        def stackgen(*arg, **kwd):
            stack = [
                ProtectIncomplete(),
                ProtectLocked(),
                ProtectDiskOnly(),
                ActionList()
            ]

            if type(arg[0]) is list:
                stack[-1].load_lists(arg[0])
            else:
                stack[-1].load_list(arg[0])

            return stack

    return stackgen