import config
import log

import json
import operator
import os
import random
import re
import signal
import subprocess
import time

import requests
import oursql

from math import sqrt

rs = requests.Session()
rs.headers.update({'User-Agent': 'pbot'})
db = oursql.connect(db='eve', user='eve', passwd='eve', autoreconnect=True)

def reload(bot, target, nick, command, text):
	import sys
	import imp
	if config.settings['owner'] == nick:
		if config.settings['autoreload']:
			bot.notice(nick, 'not reloading: autoreload is on')
			return
		imp.reload(sys.modules[__name__])
		bot.notice(nick, 'reloaded!')

def price_check(bot, target, nick, command, text):
	def get_prices(typeid, system=None, region=None):
		from xml.dom import minidom
		import xml.parsers.expat

		url = 'http://api.eve-central.com/api/marketstat'
		params = {'typeid': typeid}
		if system: params['usesystem'] = system
		if region: params['regionlimit'] = region
		try:
			xml = minidom.parseString(rs.get(url, params=params).text)
		except xml.parsers.expat.ExpatError:
			return None

		buy = xml.getElementsByTagName('buy')[0]
		buy_max = buy.getElementsByTagName('max')[0]
		bid = float(buy_max.childNodes[0].data)

		sell = xml.getElementsByTagName('sell')[0]
		sell_min = sell.getElementsByTagName('min')[0]
		ask = float(sell_min.childNodes[0].data)

		all_orders = xml.getElementsByTagName('all')[0]
		all_volume = all_orders.getElementsByTagName('volume')[0]
		volume = int(all_volume.childNodes[0].data)

		return bid, ask, volume
	def __item_info(curs, query):
		curs.execute(
				'SELECT typeID, typeName FROM invTypes WHERE typeName LIKE ?',
				(query,)
				)
		results = curs.fetchmany(3)
		if len(results) == 1:
			return results[0]
		if len(results) == 2 and \
				results[0][1].endswith('Blueprint') ^ results[1][1].endswith('Blueprint'):
			# an item and its blueprint; show the item
			if results[0][1].endswith('Blueprint'):
				return results[1]
			else:
				return results[0]
		if len(results) >= 2:
			return results
	def item_info(item_name):
		with db.cursor() as curs:
			# exact match
			curs.execute(
					'SELECT typeID, typeName FROM invTypes WHERE typeName LIKE ?',
					(item_name,)
					)
			result = curs.fetchone()
			if result:
				return result

			# start of string match
			results = __item_info(curs, item_name + '%')
			if isinstance(results, tuple):
				return results
			if results:
				names = map(lambda r: r[1], results)
				bot.say(target, 'Found items: ' + ', '.join(names))
				return

			# substring match
			results = __item_info(curs, '%' + item_name + '%')
			if isinstance(results, tuple):
				return results
			if results:
				names = map(lambda r: r[1], results)
				bot.say(target, 'Found items: ' + ', '.join(names))
				return
			bot.say(target, 'Item not found')
	def format_prices(prices):
		if prices is None:
			return 'n/a'
		if prices[1] < 1000.0:
			return 'bid {0:g} ask {1:g} vol {2:,d}'.format(*prices)
		prices = map(int, prices)
		return 'bid {0:,d} ask {1:,d} vol {2:,d}'.format(*prices)

	if text.lower() == 'plex':
		text = "30 Day Pilot's License Extension (PLEX)"
	result = item_info(text)
	if not result:
		return
	typeid, item_name = result
	jita_system = 30000142
	amarr_system = 30002187
	jita_prices = get_prices(typeid, system=jita_system)
	amarr_prices = get_prices(typeid, system=amarr_system)
	jita = format_prices(jita_prices)
	amarr = format_prices(amarr_prices)
	bot.say(target, '%s - Jita: %s ; Amarr: %s' % (item_name, jita, amarr))

def jumps(bot, target, nick, command, text):
	split = text.split()
	if len(split) != 2:
		bot.say('usage: %s [from] [to]' % command)
		return
	with db.cursor() as curs:
		curs.execute('''
				SELECT solarSystemName FROM mapSolarSystems
				WHERE solarSystemName LIKE ? or solarSystemName LIKE ?
				''', (split[0] + '%', split[1] + '%')
		)
		results = list(map(operator.itemgetter(0), curs.fetchmany(2)))
	query = [None, None]
	for i, s in enumerate(split):
		s = s.lower()
		for r in results:
			if r.lower().startswith(s):
				query[i] = r
				break
		else:
			bot.say(target, '%s: could not find system starting with %s' % (nick, s))
			break
	if None in query:
		return
	r = rs.get('http://api.eve-central.com/api/route/from/%s/to/%s' % (query[0], query[1]))
	try:
		jumps = r.json()
	except ValueError:
		bot.say(target, '%s: error getting jumps' % nick)
		return
	jumps_split = []
	for j in jumps:
		j_str = j['to']['name']
		from_sec = j['from']['security']
		to_sec = j['to']['security']
		if from_sec != to_sec:
			j_str += ' (%0.1g)' % to_sec
		jumps_split.append(j_str)
	bot.say(target, '%d jumps: %s' % (len(jumps), ', '.join(jumps_split)))

entity_re = re.compile(r'&(#?)(x?)(\w+);')
def calc(bot, target, nick, command, text):
	import codecs
	import html.entities
	def substitute_entity(match):
		ent = match.group(3)
		if match.group(1) == "#":
			if match.group(2) == '':
				return chr(int(ent))
			elif match.group(2) == 'x':
				return chr(int('0x'+ent, 16))
		else:
			cp = html.entities.name2codepoint.get(ent)
			if cp:
				return chr(cp)
			return match.group()
	def decode_htmlentities(string):
		return entity_re.subn(substitute_entity, string)[0]

	if not text:
		return
	response = rs.get('http://www.wolframalpha.com/input/', params={'i': text}).text
	matches = re.findall('context\.jsonArray\.popups\.pod_....\.push\((.*)\);', response)
	if len(matches) < 2:
		bot.say(target, nick + ': Error calculating.')
		return
	input_interpretation = json.loads(matches[0])['stringified']
	result = json.loads(matches[1])['stringified']
	output = '%s = %s' % (input_interpretation, result)
	output = output.replace('\u00a0', ' ') # replace nbsp with space
	output = codecs.getdecoder('unicode_escape')(output)[0]
	output = re.subn('<sup>(.*)</sup>', r'^(\1)', output)[0]
	output = decode_htmlentities(output)
	bot.say(target, '%s: %s' % (nick, output))

def roll(bot, target, nick, command, text):
	dice = 1
	size = 6

	split = text.split('d', 1)

	if len(split) == 2:
		try:
			dice = int(split[0])
			size = int(split[1])
		except ValueError:
			bot.say(target, 'usage: ' + 'roll [1d6]')

	if not (1 <= dice <= 10) or not (1 < size <= 100):
		bot.say(target, nick + ': Valid: 1d2 to 10d100')
		return

	results = [random.randint(1, size) for i in range(dice)]
	result = "%dd%d: " % (dice, size) + ', '.join(str(i) for i in results)
	if dice == 1:
		bot.say(target, result)
	else:
		bot.say(target, "%s; total: %d" % (result, sum(results)))

def ly(bot,target, nick, command, text):	
	split = text.split()
	if len(split) != 2:
		bot.say('usage: %s [from] [to]' % command)
		return
	with db.cursor() as curs:
		curs.execute('''
					SELECT x, y, z
					FROM mapSolarSystems
					WHERE lower(solarSystemName) LIKE %s
					OR lower(solarSystemName) LIKE %s;
					''', (split[0], split[1]))
		result = curs.fetchmany(2)
	try:
		d = sqrt(sum([(a-b)**2 for a,b in zip(*result)]) ) / 9.4605284e15
	except ValueError:
		return
	bot.say(target,"{0:.3f}".format(d))


handlers = {
	'pc': price_check,
	'jumps': jumps,
	'reload': reload,
	'calc': calc,
	'roll': roll,
}

youtube_re = re.compile('((youtube\.com\/watch\?\S*v=)|(youtu\.be/))([a-zA-Z0-9-_]+)')
def youtube(bot, msg):
	match = youtube_re.search(msg.text)
	if match is None:
		return
	vid = match.group(4)
	params = {
		'id': vid,
		'part': 'contentDetails,snippet',
		'key': 'AIzaSyAehOw6OjS2ofPSSo9AerCGuBzStsX5tks',
	}
	response = rs.get('https://www.googleapis.com/youtube/v3/videos', params=params)
	if response.status_code == 400:
		bot.say(msg.target, "%s: invalid id" % msg.nick)
		return
	video = response.json()['items'][0]
	title = video['snippet']['title']
	duration = video['contentDetails']['duration']
	duration = duration[2:].replace('H', 'h ').replace('M', 'm ').replace('S', 's')
	date = video['snippet']['publishedAt'].split('T', 1)[0]
	bot.say(msg.target, "%s's video: %s, %s, %s" % (msg.nick, title, duration, date))

def python_inline(bot, msg):
	code = msg.text[4:]
	bot.say(msg.target, '%s: %s' % (msg.nick, python(code)))

def python_multiline(bot, msg):
	lines = bot.scripts[msg.nick]
	indent = 0
	for i, line in enumerate(lines):
		for j, char in enumerate(line):
			if char != ' ':
				break
		if j < indent:
			lines[i] = '\n' + line
		indent = i
	code = '\n'.join(bot.scripts[msg.nick]) + '\n\n'
	bot.say(msg.target, '%s: %s' % (msg.nick, python(code)))

PATH = os.environ['PATH']
username = os.getlogin()
PATH = ':'.join(filter(lambda p: username not in p, PATH.split(':'))) # filter out virtualenv
def python(code):
	pypy = subprocess.Popen(['pypy-sandbox'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
			stderr=subprocess.PIPE, env={'PATH': PATH}, universal_newlines=True, preexec_fn=os.setpgrp)
	try:
		stdout, stderr = pypy.communicate(code, 5)
	except subprocess.TimeoutExpired:
		os.killpg(pypy.pid, signal.SIGKILL)
		return 'timed out after 5 seconds'
	errlines = stderr.split('\n')
	if len(errlines) > 3:
		for i in range(1, len(errlines)):
			line = errlines[-i] # iterate backwards
			if line:
				return line[:250]
	else:
		for line in stdout.split('\n'):
			if line.startswith('>>>> '):
				while line[:5] in ['>>>> ', '.... ']:
					line = line[5:]
				return line[:250]

last_kill_id = rs.get('http://api.whelp.gg/last').json()['kill_id']
last_whelp_time = time.time()
def whelp(bots):
	from bot import STATE
	import traceback
	global last_kill_id, last_whelp_time

	if time.time() < last_whelp_time + 60:
		return
	try:
		kills = rs.get('http://api.whelp.gg/last/' + str(last_kill_id)).json()
		notify = []
		for k in kills:
			try:
				item_hull_ratio = (k['total_cost'] - k['hull_cost']) // k['hull_cost']
			except ZeroDivisionError:
				item_hull_ratio = 0
			# total > 30 billion or (total > 500 million and ratio > 7)
			if k['total_cost'] > 30e9 * 100 or (k['total_cost'] > 500e6 * 100 and item_hull_ratio > 7):
				notify.append(k)
			if k['kill_id'] > last_kill_id:
				last_kill_id = k['kill_id']

		for b in bots:
			if b.state == STATE.IDENTIFIED and '#ellipsis' in b.config.channels:
				for k in notify:
					cost = '{:,d}'.format(k['total_cost'] // 100 // int(1e6))
					line = '%s million ISK %s    http://www.whelp.gg/kill/%d' % (cost, k['ship_name'], k['kill_id'])
					b.say('#ellipsis', line)
		last_whelp_time = time.time()
	except:
		log.write(traceback.format_exc())
