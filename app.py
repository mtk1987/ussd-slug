#!/usr/bin/env python
# vim: ai ts=4 sts=4 et sw=4 encoding=utf-8
from __future__ import with_statement

try:
    # Python 2.5 requires simplejson library installation
    # http://pypi.python.org/pypi/simplejson
    import simplejson as json
except ImportError:
    # Python 2.6 and 2.7 includes json library
    import json

from datetime import datetime
import threading
import time

from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned

import rapidsms
from ussd.models import *


class App(rapidsms.app.App):
    def start(self):
        # TODO OS agnostic!
        mobile_networks_file = 'apps/ussd/mobile_networks.json'
        with open(mobile_networks_file, 'r') as f:
            setattr(self, "mobile_networks", json.load(f))
        self.info("[purchaser] Starting up...")
        purchaser_interval = 10
        purchaser_thread = threading.Thread(target=self.purchaser_loop, args=(purchaser_interval,))
        purchaser_thread.daemon = True
        purchaser_thread.start()

    def parse(self, message):
        pass

    def _run_ussd(self, backend_slug, ussd_string):
        """ Given a backend slug and USSD string, gets backend from router and executes USSD string."""
        backend = self.router.get_backend(backend_slug)
        return backend._Backend__run_ussd(ussd_string)

    def _get_network_by(self, field, search):
        """ Find a network operator's JSON object by field name and value. """
        for network in self.mobile_networks:
            f = network.get(field)
            if f is not None:
                if f == search:
                    return network
        return None

    def _get_network_by_identity(self, identity):
        """ Find a network operator's JSON object by identity. """
        for network in self.mobile_networks:
            identities = network.get("Operator Identities")
            if identities is not None:
                if identity in identities:
                    return network
        return None

    def update_balances(self):
        self.debug('updating balances...')
        sims = SIM.objects.all()
        balances = {}
        for sim in sims:
            self.debug(sim.operator_name)
            b = self.check_balance(sim)
            sim.balance = b
            sim.save()
            balances.update({sim.operator_name: b})
        return balances

    def check_balance(self, sim):
        self.debug('checking balance...')

    network = self._get_network_by("Operator Short", sim.operator_name)
    if network is not None:
        result = self._run_ussd(sim.backend.slug, network["USSD Balance"])
        if result is not None:
            self.debug(result)
            notice = OperatorNotification(sim=sim, type='B', text=result, identity='USSD')
            notice.save()
            result_list = result.split()
            # return the first token that is a number and hope it is the airtime balance
            for token in result_list:
                if token.isdigit():
                    return token
            # if there is no number in result return whole string so that it can be reviewed via web
            return result
        return "Unknown! Please try again later."


def recharge_airtime(self, sim):
    self.debug('recharging airtime...')
    # TODO
    pass


def purchase_airtime_bundle(self, sim, destination, amount, pin="", force=False):
    self.debug('purchasing airtime bundle...')
    network = self._get_network_by("Operator Short", sim.operator_name)
    # messages confirming purchases of bundles can be vague
    # often not containing the intended destination
    # so we will only initiate a new bundle purchase
    # if there are no outstanding purchases expecting a notification message
    # if you are impatient, you may force a new purchase in spite of pending purchases
    # but its probably best to change status of pending purchases to 'unknown' instead
    if self.pending_bundle_purchase(network) is None or force:
        if network is not None:
            # TODO destination number must not include international prefix -- be more smarter than this...
            if destination.startswith('+'):
                return "Please try again without international prefix"

            # assemble ussd_string
            ussd_string = network["USSD Bundle Purchase"] % {'destination': destination, 'amount': amount, 'PIN': pin}

            # execute
            result = self._run_ussd(sim.backend.slug, ussd_string)
            self.debug('ussd executed!')
            if result is not None:
                #TODO import result code dict from pygsm?
                if not result.startswith('operation'):
                    self.debug(result)
                    #did we run a queued purchase?
                    purch = \
                    AirtimeBundlePurchase.objects.filter(destination=destination, amount=amount, sim=sim, status='Q')[0]
                    if not purch:
                        purch = AirtimeBundlePurchase(destination=destination, amount=amount, sim=sim)
                        purch.initiated = datetime.now()
                        purch.status = 'P'
                        purch.save()
                        self.debug(purch)
                    return result
            else:
                return "Please try again later!"

    def handle(self, message):
        if message.text.lower().startswith("balance"):
            self.debug(self.update_balances())
        if message.text.lower().startswith("buy"):
            self.debug(self.buy_day_bundle())

        network = self._get_network_by_identity(message.peer)
        if network is not None:
            return self.process_notification(message, network)

    def buy_day_bundle(self):
        # Buy 100MB daily bundle.
        sim = SIM.objects.all()[0]
        return self.purchase_airtime_bundle(sim, "0964571227", " 100MB")

    def process_notification(self, message, network):
        self.debug('processing notification...')
        self.debug(message.connection.identity)
        self.debug(message.text)

        # if the notification prefix numberings are any indication
        # there may be thousands of kinds of notification messages...
        notice_type = 'U'

        # TODO these are MTN specific
        if message.text.startswith('302'):
            # airtime bundle was purchased
            notice_type = 'R'
        if message.text.startswith('3049'):
            # purchase attempt failed
            notice_type = 'F'
        if message.text.startswith('301'):
            # purchase successful
            notice_type = 'S'

        sim = SIM.objects.get(operator_name=network["Operator Short"])

        notification = OperatorNotification(text=message.text, identity=message.peer, type=notice_type, sim=sim)
        notification.save()

        pending = self.pending_bundle_purchase(network)
        self.debug(pending)
        if isinstance(pending, AirtimeBundlePurchase):
            pending.notification = notification
            # mark airtime bundle purchase with appropriate status
            if notification.type in ['U', 'S', 'F']:
                pending.status = notification.type
            else:
                pending.status = 'U'
            pending.save()

    def pending_bundle_purchase(self, network):
        self.debug('finding pending purchase...')
        try:
            pending_bundle_purchase = AirtimeBundlePurchase.objects.get(sim_operator_name=network["Operator Short"],
                                                                        status='P')
            self.debug('FOUND:')
            self.debug(pending_bundle_purchase)
            return pending_bundle_purchase
        except MultipleObjectsReturned:
            self.debug('many pending purchases!')
            return "MADNESS"
        except ObjectDoesNotExist:
            self.debug('no pending purchase')
            return None

    def wait_until_confirmation_or_timeout(self):
        # TODO only for one SIM
        pauses = 0
        timeout = 10
        while pauses < timeout:
            if AirtimeBundlePurchase.objects.filter(status='P').count() > 0:
                # hang out for a while as there are outstanding transfers
                pauses += 1
                time.sleep(10)
                continue
            else:
                # no pending purchases
                return True
        # If there's still no confirmation mark pending purchases as unknown
        AirtimeBundlePurchase.objects.filter(status='P').update(status='U')
        # give caller green light
        return True

    def ajax_POST_purchase(self, params, form):
        self.debug(form)
        sim = SIM.objects.get(pk=int(form['sim']))
        return self.purchase_airtime_bundle(sim, str(form['destination']), str(form['amount']))

        def ajax_POST_balance(self, params, form):
            return self.update_balances()

            # Purchaser Thread ------------------------

        def purchaser_loop(self, seconds=10):
            self.info("Starting purchaser loop...")
            # pause so that we don't run USSD codes before pygsm has booted
            for purchase in AirtimeBundlePurchase.objects.filter(status='Q'):
                self.info("Purchasing %s to %s." % (purchase.amount, purchase.destination))
                sim = SIM.objects.get(pk=purchase.sim.pk)
                purch = self.purchase_airtime_bundle(sim, purchase.destination, purchase.amount)
                if self.wait_until_confirmation_or_timeout():
                    continue
            # wait until it is time to check again
            time.sleep(seconds)
