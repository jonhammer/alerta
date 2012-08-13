#!/usr/bin/env python
########################################
#
# alerta-dbapi.py - Alerter DB API
#
########################################

import os
import sys
try:
    import json
except ImportError:
    import simplejson as json
import time
import datetime
import stomp
import yaml
import pymongo
import urlparse
import logging
import pytz
import re

__version__ = '1.8.0'

BROKER_LIST  = [('localhost', 61613)] # list of brokers for failover
NOTIFY_TOPIC = '/topic/notify'
EXPIRATION_TIME = 600 # seconds = 10 minutes

CONFIGFILE = '/opt/alerta/conf/alerta-global.yaml'
LOGFILE = '/var/log/alerta/alert-dbapi.log'

# Extend JSON Encoder to support ISO 8601 format dates
class DateEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.replace(microsecond=0).isoformat() + ".%03dZ" % (obj.microsecond//1000)
        else:
            return json.JSONEncoder.default(self, obj)

def main():

    start = time.time()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s alert-dbapi[%(process)d] %(levelname)s - %(message)s", filename=LOGFILE)
    logging.info('Received HTTP request %s %s' % (os.environ['REQUEST_METHOD'], os.environ['REQUEST_URI']))

    total = 0
    status = dict()
    status['response'] = dict()
    status['response']['status'] = None
    error = 'unknown error'

    for e in os.environ:
        logging.debug('%s: %s', e, os.environ[e])

    # Get HTTP method and any body data
    method = os.environ['REQUEST_METHOD']
    if method in ['PUT', 'POST']:
        try:
            data = json.loads(sys.stdin.read())
        except ValueError, e:
            data = list()
            logging.warning('Failed to get data - %s', e)
            error = 'failed to parse json data in body'
        if '_method' in data:                  # for clients that don't support a DELETE, use POST with "_method: delete"
            method = data['_method'].upper()

    # Parse RESTful URI
    uri = urlparse.urlsplit(os.environ['REQUEST_URI'])
    form = urlparse.parse_qs(os.environ['QUERY_STRING'])
    request = method + ' ' + uri.path

    # Connect to MongoDB
    mongo = pymongo.Connection()
    db = mongo.monitoring
    alerts = db.alerts
    mgmt = db.status
    query = dict()

    # Read in config file
    try:
        config = yaml.load(open(CONFIGFILE,'r'))
    except IOError, e:
        logging.error('Failed to load config file %s: %s', CONFIGFILE, e)
    if config and 'warning' in config:
        status['response']['warning'] = config['warning']

    m = re.search(r'GET /alerta/api/v1/alerts/alert/(?P<id>[a-z0-9-]+)$', request)
    if m:
        query['_id'] = m.group('id')

        status['response']['alert'] = list()

        logging.debug('MongoDB GET -> alerts.find_one(%s)', query)
        alert = alerts.find_one(query)
        if alert:
            alert['id'] = alert['_id']
            del alert['_id']
            status['response']['alert'] = alert
            status['response']['status'] = 'ok'
            total = 1
        else:
            status['response']['alert'] = None
            status['response']['status'] = 'not found'
            total = 0

        diff = time.time() - start
        status['response']['time'] = "%.3f" % diff
        status['response']['total'] = total
        status['response']['localTime'] = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        diff = int(diff * 1000) # management status needs time in milliseconds
        mgmt.update(
            { "group": "requests", "name": "simple_get", "type": "counter", "title": "Simple GET requests", "description": "Requests to the alert status API" },
            { '$inc': { "count": 1, "totalTime": diff}},
            True)

    m = re.search(r'GET /alerta/api/v1/alerts$', request)  # hide-alert-details, sort-by 
    if m:
        logging.debug('form %s' % form)

        query = dict()
        for field in form:
            if field in ['callback', '_', 'sort-by', 'hide-alert-details', 'limit', 'from-date']:
                continue
            if field == 'id':
                query['_id'] = dict()
                query['_id']['$regex'] = '^'+form['id'][0]
            elif len(form[field]) == 1:
                query[field] = dict()
                query[field]['$regex'] = form[field][0]
                query[field]['$options'] = 'i'  # case insensitive search
            else:
                query[field] = dict()
                query[field]['$in'] = form[field]

        if 'hide-alert-details' in form:
            hide_details = form['hide-alert-details'][0] == 'true'
        else:
            hide_details = False

        if 'limit' in form:
            limit = int(form['limit'][0])
        else:
            limit = 0

        if 'from-date' in form:
            from_date = datetime.datetime.strptime(form['from-date'][0], '%Y-%m-%dT%H:%M:%S.%fZ')
            from_date = from_date.replace(tzinfo=pytz.utc)
            to_date = datetime.datetime.utcnow()
            to_date = to_date.replace(tzinfo=pytz.utc)
            query['lastReceiveTime'] = {'$gte': from_date, '$lt': to_date }

        sortby = list()
        if 'sort-by' in form:
            for s in form['sort-by']:
                if s in ['createTime', 'receiveTime', 'lastReceiveTime']:
                    sortby.append((s,-1)) # sort by newest first
                else:
                    sortby.append((s,1)) # sort by newest first
        else:
            sortby.append(('lastReceiveTime',-1))

        # Init status and severity counts
        total = 0
        opened = 0
        ack = 0
        closed = 0
        critical = 0
        major = 0
        minor = 0
        warning = 0
        normal = 0
        inform = 0
        debug = 0

        alertDetails = list()
        logging.debug('MongoDB GET all -> alerts.find(%s, sort=%s).limit(%s)', query, sortby, limit)
        for alert in alerts.find(query, sort=sortby).limit(limit):
            if not hide_details:
                alert['id'] = alert['_id']
                del alert['_id']
                alertDetails.append(alert)

            total += 1
            if alert['status'] == 'OPEN':
                opened += 1
            if alert['status'] == 'ACK':
                ack += 1
            if alert['status'] == 'CLOSED':
                closed += 1

            # Only OPEN alerts contribute to the severity counts
            if alert['status'] != 'OPEN':
                continue
            if alert['severity'] == 'CRITICAL':
                critical += 1
            elif alert['severity'] == 'MAJOR':
                major += 1
            elif alert['severity'] == 'MINOR':
                minor += 1
            elif alert['severity'] == 'WARNING':
                warning += 1
            elif alert['severity'] == 'NORMAL':
                normal += 1
            elif alert['severity'] == 'INFORM':
                inform += 1
            elif alert['severity'] == 'DEBUG':
                debug += 1

        stat = { 'open': opened,
                   'ack': ack,
                   'closed': closed
        }
        logging.info('statusCounts %s', stat)

        sev = { 'critical': critical,
                'major': major,
                'minor': minor,
                'warning': warning,
                'normal': normal,
                'inform': inform,
                'debug': debug
        }
        logging.info('severityCounts %s', sev)

        status['response']['alerts'] = { 'statusCounts': stat, 'severityCounts': sev, 'alertDetails': list(alertDetails) }

        diff = time.time() - start
        status['response']['status'] = 'ok'
        status['response']['time'] = "%.3f" % diff
        status['response']['total'] = total
        status['response']['localTime'] = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        diff = int(diff * 1000) # management status needs time in milliseconds
        mgmt.update(
            { "group": "requests", "name": "complex_get", "type": "timer", "title": "Complex GET requests", "description": "Requests to the alert status API" },
            { '$inc': { "count": 1, "totalTime": diff}},
            True)

    m = re.search(r'PUT /alerta/api/v1/alerts/alert/(?P<id>[a-z0-9-]+)$', request)
    if m:
        alertid = m.group('id')
        query['_id'] = alertid
        update = data

        logging.debug('MongoDB MODIFY -> alerts.update(%s, { $set: %s })', query, update)
        error = alerts.update(query, { '$set': update }, safe=True)
        logging.debug('MongoDB MODIFY -> error %s', error)
        if error['updatedExisting'] == True:
            status['response']['status'] = 'ok'

            if 'status' in update:
                updateTime = datetime.datetime.utcnow()
                updateTime = updateTime.replace(tzinfo=pytz.utc)
                alerts.update(query, { '$push': { "history": { "status": update['status'], "updateTime": updateTime } }})

                # Forward status update to notify topic and logger queue
                alert = alerts.find_one({"_id": alertid}, {"_id": 0, "history": 0})

                headers = dict()
                headers['type']           = alert['type']
                headers['correlation-id'] = alertid
                headers['persistent']     = 'true'
                headers['expires']        = int(time.time() * 1000) + EXPIRATION_TIME * 1000
                headers['repeat']         = 'false'

                alert['id'] = alertid

                try:
                    conn = stomp.Connection(BROKER_LIST)
                    conn.start()
                    conn.connect(wait=True)
                except Exception, e:
                    print >>sys.stderr, "ERROR: Could not connect to broker - %s" % e
                    logging.error('Could not connect to broker %s', e)
                try:
                    logging.info('%s : Fwd alert to %s', alertid, NOTIFY_TOPIC)
                    conn.send(json.dumps(alert, cls=DateEncoder), headers, destination=NOTIFY_TOPIC)
                except Exception, e:
                    print >>sys.stderr, "ERROR: Failed to send alert to broker - %s " % e
                    logging.error('Failed to send alert to broker %s', e)
                broker = conn.get_host_and_port()
                logging.info('%s : Alert sent to %s:%s', alertid, broker[0], str(broker[1]))
                conn.disconnect()
        else:
            status['response']['status'] = 'error'
            status['response']['message'] = 'No existing alert with that ID found'

        diff = time.time() - start
        status['response']['time'] = "%.3f" % diff
        status['response']['localTime'] = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        diff = int(diff * 1000) # management status needs time in milliseconds
        mgmt.update(
            { "group": "requests", "name": "update", "type": "timer", "title": "PUT requests", "description": "Requests to update alerts via the API" },
            { '$inc': { "count": 1, "totalTime": diff}},
            True)

    m = re.search(r'PUT /alerta/api/v1/alerts/alert/(?P<id>[a-z0-9-]+)/tag$', request)
    if m:
        query['_id'] = m.group('id')
        tag = data

        logging.info('MongoDB TAG -> alerts.update(%s, { $push: %s })', query, tag)
        error = alerts.update(query, { '$push': tag }, safe=True)
        if error['ok'] == 1:
            status['response']['status'] = 'ok'

        diff = time.time() - start
        status['response']['time'] = "%.3f" % diff
        status['response']['localTime'] = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        diff = int(diff * 1000) # management status needs time in milliseconds
        mgmt.update(
            { "group": "requests", "name": "update", "type": "timer", "title": "PUT requests", "description": "Requests to update alerts via the API" },
            { '$inc': { "count": 1, "totalTime": diff}},
            True)

    m = re.search(r'DELETE /alerta/api/v1/alerts/alert/(?P<id>\S+)$', request)
    if m:
        query['_id'] = m.group('id')

        logging.info('MongoDB DELETE -> alerts.remove(%s)', query)
        error = alerts.remove(query, safe=True)
        if error['ok'] == 1:
            status['response']['status'] = 'ok'

        diff = time.time() - start
        status['response']['time'] = "%.3f" % diff
        status['response']['localTime'] = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        diff = int(diff * 1000) # management status needs time in milliseconds
        mgmt.update(
            { "group": "requests", "name": "delete", "type": "timer", "title": "DELETE requests", "description": "Requests to delete alerts via the API" },
            { '$inc': { "count": 1, "totalTime": diff}},
            True)

    if status['response']['status'] == None:

        logging.error('Failed request %s', request)

        diff = time.time() - start
        status['response']['time'] = "%.3f" % diff
        status['response']['status'] = 'error'
        status['response']['message'] = error
        status['response']['localTime'] = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        diff = int(diff * 1000)
        mgmt.update(
            { "group": "requests", "name": "bad", "type": "timer", "title": "Bad requests", "description": "Failed requests to the API" },
            { '$inc': { "count": 1, "totalTime": diff}},
            True)

    content = json.dumps(status, cls=DateEncoder)
    if 'callback' in form:
        content = '%s(%s);' % (form['callback'][0], content)

    print "Content-Type: application/javascript; charset=utf-8"
    print "Content-Length: %s" % len(content)
    print "Expires: -1"
    print "Cache-Control: no-cache"
    print "Pragma: no-cache"
    print ""
    print content

    logging.info('Request %s completed in %sms', request, diff)

if __name__ == '__main__':
    main()
