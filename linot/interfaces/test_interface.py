# -*- coding: utf-8 -*-
"""Example interface implementation for testing"""
import sys
from collections import defaultdict

from linot.base_interface import BaseInterface


class TestInterface(BaseInterface):
    NAME = 'test'

    def __init__(self):
        self.reset()

    def polling_command(self):
        for sender, cmd in self.command_queue:
            if self.polling_callback is not None:
                self.polling_callback()
            yield sender, cmd
        self.command_queue = []

    def send_message(self, receiver, msg):
        self.msg_queue[receiver.code].append(msg)
        return True

    def get_display_name(self, submitter):
        name = '<{}>{}'.format(self.NAME, submitter.code)
        return name

    def reset(self):
        self.msg_queue = defaultdict(list)
        self.command_queue = []
        self.polling_callback = None

    def add_command(self, sender, cmd):
        assert sender.interface_name == self.NAME
        self.command_queue.append((sender, cmd))

    def add_command_list(self, cmd_list):
        for sender, cmd in cmd_list:
            self.add_command(sender, cmd)

    def set_polling_commad_callback(self, func):
        self.polling_callback = func

# register interface only when runnig testcases with nose
if 'nose' not in sys.modules:
    TestInterfaces = None  # avoid being imported into interface list
