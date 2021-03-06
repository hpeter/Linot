from __future__ import print_function
from collections import defaultdict
from threading import Thread, Event, Lock
import pickle
import argparse
import re
import copy
import io

import linot.config as config
from .twitch_engine import TwitchEngine
from linot.services.service_base import ServiceBase
import linot.logger
logger = linot.logger.getLogger(__name__)


class Checker(Thread):
    def __init__(self, period, twitch, get_sublist):
        super(Checker, self).__init__()
        self._stop = Event()
        self._polling = Event()
        self._period = period
        self._twitch = twitch
        self._get_sublist = get_sublist
        self._status_lock = Lock()

    def run(self):
        logger.info('Twitch Checker is started')
        # Skip 1st notify if channels are already live before plugin load
        self._set_live_channels(self._twitch.get_live_channels())
        while(not self._stop.is_set()):
            logger.debug('Wait polling {} sec.'.format(self._period))
            self._polling.wait(self._period)
            logger.debug('Polling event is triggered.')
            self._polling.clear()
            logger.debug('Try get live channels')
            current_live_channels = self._twitch.get_live_channels()
            logger.debug('Live Channels: ' + str(current_live_channels.viewkeys()))
            local_live_channels = self.get_live_channels()
            logger.debug('Previous live Channels: ' + str(local_live_channels.viewkeys()))
            off_channels = local_live_channels.viewkeys() - current_live_channels.viewkeys()
            for ch in off_channels:
                # TODO do we have to notify user the channel went off?
                del local_live_channels[ch]
            new_live_channels = current_live_channels.viewkeys() - local_live_channels.viewkeys()
            # Send live notifications to subcribers
            for ch in new_live_channels:
                local_live_channels[ch] = current_live_channels[ch]
                local_sublist = self._get_sublist()
                for user in local_sublist:
                    if ch in local_sublist[user]:
                        msg = io.BytesIO()
                        print('{} is now streamming!!'.format(ch), file=msg)
                        print('msg = [Title] {}'.format(current_live_channels[ch]['status']), file=msg)
                        print('[Playing] {}'.format(current_live_channels[ch]['game']), file=msg)
                        print(current_live_channels[ch]['url'], file=msg)
                        user.send_message(msg.getvalue())
            self._set_live_channels(local_live_channels)

        self._stop.clear()
        logger.info('Twitch Checker is stopped')

    def _set_live_channels(self, ch_list):
        self._status_lock.acquire(True)
        self._live_channels = ch_list
        self._status_lock.release()

    def refresh(self):
        logger.debug('Trigger refresh')
        self._polling.set()

    def get_live_channels(self):
        self._status_lock.acquire(True)
        ch_stat = copy.copy(self._live_channels)
        self._status_lock.release()
        return ch_stat

    def async_stop(self):
        logger.debug('stop is called')
        self._polling.set()
        self._stop.set()

    def stop(self):
        self.async_stop()
        logger.debug('waiting for thread end')
        self.join()

    def is_stopped(self):
        return self._stop.isSet()


class Service(ServiceBase):
    SUB_FILE = 'twitch_sublist.p'
    CHECK_PERIOD = 300

    def __init__(self):
        ServiceBase.__init__(self)
        self.cmd = 'twitch'
        self._sublist_lock = Lock()
        self._twitch = TwitchEngine()

    def _setup_argument(self, cmd_group):
        cmd_group.add_argument('-subscribe', nargs='+', func=self._subscribe,
                               help='Subscribe channels')
        cmd_group.add_argument('-unsubscribe', nargs='+', func=self._unsubscribe,
                               help='Unsubscribe channels')
        cmd_group.add_argument('-listchannel', action='store_true', func=self._list_channel,
                               help='List channels you\'ve subscribed')
        cmd_group.add_argument('-refresh', action='store_true', func=self._refresh,
                               help=argparse.SUPPRESS)
        cmd_group.add_argument('-listusers', action='store_true', func=self._list_users,
                               help=argparse.SUPPRESS)
        cmd_group.add_direct_command(self._sub_by_url, 'twitch\.tv/(\w+)[\s\t,]*', re.IGNORECASE)

    def _start(self):
        # Load subscribe list
        try:
            logger.debug('Loading subscribe list from file')
            self._sublist = pickle.load(open(self.SUB_FILE, 'rb'))
            self._calculate_channel_sub_count()
        except IOError:
            logger.debug('Subscribe list file not found, create empty.')
            self._sublist = defaultdict(list)
            self._channel_sub_count = defaultdict(int)
        self._check_thread = Checker(
            self.CHECK_PERIOD, self._twitch, self.get_sublist)
        self._check_thread.start()

    def _stop(self):
        self._check_thread.stop()

    def get_sublist(self):
        self._sublist_lock.acquire(True)
        local_sublist = copy.copy(self._sublist)
        self._sublist_lock.release()
        return local_sublist

    def _sub_by_url(self, match_iter, cmd, sender):
        logger.debug('sub by url: ' + str(match_iter))
        logger.debug('sub by url, direct cmd: ' + cmd)
        self._subscribe(match_iter, sender)

    def _calculate_channel_sub_count(self):
        self._channel_sub_count = defaultdict(int)
        for subr in self._sublist:
            for ch in self._sublist[subr]:
                self._channel_sub_count[ch] += 1

    def _subscribe(self, chs, sender):
        # Handles user request for subscribing channels
        # We actually let the LinotServant to follow these channels
        # so that we can check if they are online use streams/followed API
        not_found = []
        for ch in chs:
            # reduce api invocation
            # TODO fix this
            if ch in self._sublist[sender]:  # pragma: no cover
                continue
            ch_disp_name, stat = self._twitch.follow_channel(ch)
            if stat is False:
                not_found.append(ch)
            else:
                self._sublist_lock.acquire(True)
                if ch_disp_name not in self._sublist[sender]:  # TODO fix this
                    self._sublist[sender].append(ch_disp_name)
                self._sublist_lock.release()
                self._channel_sub_count[ch_disp_name] += 1
                pickle.dump(self._sublist, open(self.SUB_FILE, 'wb+'))

        if len(not_found) > 0:
            sender.send_message('Channel not found: ' + ' '.join(not_found))
        sender.send_message('Done')
        return

    def _unsubscribe(self, chs, sender):
        # Handles user request for unsubscribing channels
        not_found = []
        for ch in chs:
            self._sublist_lock.acquire(True)
            try:
                self._sublist[sender].remove(ch)
            except ValueError:
                not_found.append(ch)
                self._sublist_lock.release()
                continue
            self._sublist_lock.release()
            self._channel_sub_count[ch] -= 1
            if self._channel_sub_count[ch] <= 0:
                self._twitch.unfollow_channel(ch)
                self._channel_sub_count.pop(ch, None)
        if len(self._sublist[sender]) == 0:
            self._sublist_lock.acquire(True)
            self._sublist.pop(sender)
            self._sublist_lock.release()

        pickle.dump(self._sublist, open(self.SUB_FILE, 'wb+'))
        if len(not_found) > 0:
                sender.send_message('Channel not found: ' + ' '.join(not_found))
        sender.send_message('Done')
        return

    def _list_channel(self, value, sender):
        msg = io.BytesIO()
        print('Your subscribed channels:', file=msg)
        live_channels = self._check_thread.get_live_channels()
        for ch in self._sublist[sender]:
            if ch in live_channels:
                stat = '[LIVE]'
            else:
                stat = '[OFF]'
            print('{}\t{}'.format(stat, ch) + '\n', file=msg)
        sender.send_message(msg.getvalue())

    def _refresh(self, value, sender):
        # <Admin only>
        if sender.code == config['interface'][sender.interface_name]['admin_id']:
            self._check_thread.refresh()
            sender.send_message('Done')

    def _list_users(self, args, sender):
        # List all user who has subscription
        # <Admin only>
        if sender.code == config['interface'][sender.interface_name]['admin_id']:
            msg = io.StringIO()
            for user in self._sublist:
                print(unicode(user), file=msg)
                print(u'Channels:', file=msg)
                for ch in self._sublist[user]:
                    print(ch, end=u', ', file=msg)
                print(u'', file=msg)
                print(u'----------------------------', file=msg)
            sender.send_message(msg.getvalue())
            sender.send_message('Done')
        return
