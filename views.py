#!/usr/bin/env python
# vim: ai ts=4 sts=4 et sw=4 encoding=utf-8

import csv

from ussd.models import *
from utilities.export import export


def index(req):
    return render_to_response(req,
                              "ussd/index.html", {
            "sims": SIM.objects.all(),
            "transfers": AirtimeBundlePurchase.objects.all()
        })


def bulk_airtime(req):
    file = req.FILES['file']
    amount = req.POST['amount']
    sim = SIM.objects.get(pk=int(req.POST['sim']))

    csvee = file

    dialect = csv.Sniffer().sniff(csvee.read(1024))
    csvee.seek(0)

    reader = csv.DictReader(csvee, quoting=csv.QUOTE_ALL, dialect=dialect)

    try:
        for row in reader:
            if row.has_key('NUMERO'):
                if row['NUMERO'] != "":
                    print(row['NUMERO'])
                    # cast as strings
                    purch = AirtimeBundlePurchase(
                        destination=str(row['NUMERO']), amount=amount, status='Q', sim=sim)
                    purch.save()
                    continue
    except csv.Error, e:
        # TODO handle error?
        print('%d : %s' % (reader.reader.line_num, e))
    return HttpResponseRedirect("/ussd")


def csv_purchases(req, format='csv'):
    return export(AirtimeBundlePurchase.objects.all())
