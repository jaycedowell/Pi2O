# -*- coding: utf-8 -*

"""
Module for interfacing with the sqlite3 database.
"""

import os
import sys
import time
import uuid
import queue
import logging
import sqlite3
import threading
import traceback
from configparser import NoSectionError
from io import StringIO

__version__ = "0.3"
__all__ = ["Archive", "__version__", "__all__"]


# Logger instance
dbLogger = logging.getLogger('__main__')


class DatabaseProcessor(threading.Thread):
	"""
	Class responsible for providing access to the database from a single thread.
	"""
	
	def __init__(self, dbName):
		self._dbName = dbName
		self.running = False
		self.input = queue.Queue()
		self.output = queue.Queue()
		
		self.thread = None
		self.alive = threading.Event()
		
	def start(self):
		if self.thread is not None:
			self.cancel()
        	       
		self.thread = threading.Thread(target=self.run, name='dbAccess')
		self.thread.setDaemon(1)
		self.alive.set()
		self.thread.start()
		
		dbLogger.info('Started the DatabaseProcessor background thread')
		
	def cancel(self):
		if self.thread is not None:
			self.alive.clear()          # clear alive event for thread
			self.thread.join()
			
		dbLogger.info('Stopped the DatabaseProcessor background thread')
			
	def appendRequest(self, cmd):
		rid = str(uuid.uuid4())
		self.input.put( (rid,cmd) )
		
		return rid
		
	def getResponse(self, rid):
		qid, qresp = self.output.get()
		while qid != rid:
			self.output.put( (qid,qresp) )
			qid, qresp = self.output.get()
			
		return qresp
		
	def dict_factory(self, cursor, row):
		d = {}
		for idx, col in enumerate(cursor.description):
			d[col[0]] = row[idx]
		return d
		
	def run(self):
		self._dbConn = sqlite3.connect(self._dbName)
		self._dbConn.row_factory = self.dict_factory
		self._cursor = self._dbConn.cursor()
		
		while self.alive.isSet() or not self.input.empty():
			try:
				rid, cmd = self.input.get()
				self._cursor.execute(cmd)
				output = []
				for row in self._cursor.fetchall():
					output.append( row )
				if cmd[:6] != 'SELECT':
					self._dbConn.commit()
				self.output.put( (rid,output) )
				
			except Exception as e:
				exc_type, exc_value, exc_traceback = sys.exc_info()
				dbLogger.error("DatabaseProcessor: %s at line %i", e, traceback.tb_lineno(exc_traceback))
				## Grab the full traceback and save it to a string via StringIO
				fileObject = StringIO()
				traceback.print_tb(exc_traceback, file=fileObject)
				tbString = fileObject.getvalue()
				fileObject.close()
				## Print the traceback to the logger as a series of DEBUG messages
				for line in tbString.split('\n'):
					dbLogger.debug("%s", line)
					
		self._dbConn.close()


class Archive(object):
	_dbConn = None
	_cursor = None
	
	def __init__(self, config):
		self.config = config
		self._dbName = os.path.join(os.path.dirname(__file__), 'archive', 'pi2o-data.db')
		if not os.path.exists(self._dbName):
			raise RuntimeError("Archive database not found")
		self._backend = None
		
		# Figure out how many zones there are
		zones = []
		zone = 1
		while True:
			try:
				## Is the zone enabled?
				zoneEnabled = config.get('Zone%i' % zone, 'enabled')
				if zoneEnabled == 'on':
					### If so, use the real GPIO pin
					zonePin = config.getint('Zone%i' % zone, 'pin')
					### If not, use a dummy pin
				else:
					zonePin = -1
				
				## Create the SprinklerZone instance
				zones.append( zonePin )
			
				## Update the counter
				zone += 1
			
			except NoSectionError:
				break
		self.nZones = len(zones)
    	
	def start(self):
		"""
		Open the database.
		"""
		
		if self._backend is None:
			self._backend = DatabaseProcessor(self._dbName)
		self._backend.start()
		
	def cancel(self):
		"""
		Close the database.
		"""
	
		if self._backend is not None:
			self._backend.cancel()
			
	def getData(self, age=0, scheduledOnly=False):
		"""
		Return a collection of data a certain number of seconds into the past.
		"""
	
		# Fetch the entries that match
		if age <= 0:
			if scheduledOnly:
				sqlCmd = 'SELECT * FROM pi2o WHERE wxAdjust >= 0.0 OR wxAdjust <= -1.5 ORDER BY dateTimeStart DESC LIMIT %i' % self.nZones
			else:
				sqlCmd = 'SELECT * FROM pi2o GROUP BY zone ORDER BY dateTimeStart DESC LIMIT %i' % self.nZones
			rid = self._backend.appendRequest(sqlCmd)
		else:
			# Figure out how far to look back into the database
			tNow = time.time()
			tLookback = tNow - age
			if scheduledOnly:
				sqlCmd = 'SELECT * FROM pi2o WHERE dateTimeStart >= %i AND (wxAdjust >= 0.0 OR wxAdjust <= -1.5) ORDER BY dateTimeStart DESC' % tLookback
			else:
				sqlCmd = 'SELECT * FROM pi2o WHERE dateTimeStart >= %i ORDER BY dateTimeStart DESC' % tLookback
			rid = self._backend.appendRequest(sqlCmd)
			
		# Fetch the output
		output = self._backend.getResponse(rid)
		
		# Done
		return output
			
	def writeData(self, timestamp, zone, status, wxAdjustment=None):
		"""
		Write a collection of data to the database.
		"""
		
		# Validate
		if status not in ('on', 'off'):
			raise ValueError("Invalid status code '%s'" % status)
		if wxAdjustment is None:
			wxAdjustment = 1.0
			
		# Add the entry to the database
		if status == 'on':
			rid = self._backend.appendRequest('INSERT INTO pi2o (dateTimeStart,dateTimeStop,zone,wxAdjust) VALUES (%i,%i,%i,%f)' % (timestamp, 0, zone, wxAdjustment))
			output = self._backend.getResponse(rid)
		else:
			rid = self._backend.appendRequest('SELECT dateTimeStart FROM pi2o WHERE zone == %i AND dateTimeStop == 0 ORDER BY dateTimeStart DESC' % zone)
			output = self._backend.getResponse(rid)
			row = output[0]
			rid = self._backend.appendRequest('UPDATE pi2o SET dateTimeStop = %i WHERE dateTimeStart == %i AND zone == %i' % (timestamp, row['dateTimeStart'], zone))
			output = self._backend.getResponse(rid)
			
		return True
