#!/usr/bin/env python

import os
import sys
import sqlite3


def dict_factory(cursor, row):
	d = {}
	for idx, col in enumerate(cursor.description):
		d[col[0]] = row[idx]
	return d


def main(args):
	filename = args[0]
	
	# Load the sprinklers_pi database and convert the entries into SQL commands
	# for Pi2O
	conn = sqlite3.connect(filename)
	conn.row_factory = dict_factory
	cursor = conn.cursor()
	
	toInsert = []
	cursor.execute('SELECT * FROM zonelog ORDER BY date')
	while True:
		row = cursor.fetchone()
		if row is None:
			break
			
		start = row['date']
		stop = start + row['duration']
		zone = row['zone']
		wxAdjust = row['wunderground'] / 100.0
		
		hist = (start, stop, zone, wxAdjust)
		toInsert.append( hist )
		
	conn.close()
	
	
	# Open the Pi2O database and add the information if it doesn't already exist
	conn = sqlite3.connect('pi2o-data.db')
	conn.row_factory = dict_factory
	cursor = conn.cursor()
	
	# Insert the data if it doesn't already exist
	for hist in toInsert:
		cursor.execute("SELECT * FROM pi2o WHERE dateTimeStart == %i and dateTimeStop == %i and zone == %i" % hist[:3])
		row = cursor.fetchone()
		if row is None:
			cursor.execute("INSERT INTO pi2o (dateTimeStart,dateTimeStop,zone,wxAdjust) VALUES (%i,%i,%i,%f)" % hist)
			
	# Close it out
	conn.commit()
	conn.close()	


if __name__ == "__main__":
	main(sys.argv[1:])
	