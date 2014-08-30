"""
Module for interfacing with the sqlite3 database.
"""

import os
import time
import sqlite3

__version__ = "0.1"
__all__ = ["Archive", "__version__", "__all__"]


class Archive(object):
	_dbConn = None
	_cursor = None
	
	def __init__(self):
		self._dbName = os.path.join(os.path.dirname(__file__), 'archive', 'pi2o-data.db')
		if not os.path.exists(self._dbName):
			raise RuntimeError("Archive database not found")
			
		self.open()
		
	def dict_factory(self, cursor, row):
		d = {}
		for idx, col in enumerate(cursor.description):
			d[col[0]] = row[idx]
		return d
    	
	def open(self):
		"""
		Open the database.
		"""
		
		self._dbConn = sqlite3.connect(self._dbName)
		self._dbConn.row_factory = self.dict_factory
		self._cursor = self._dbConn.cursor()
		
	def close(self):
		"""
		Close the database.
		"""
	
		if self._dbConn is not None:
			self._dbConn.commit()
			self._dbConn.close()
			
			self._dbConn = None
		
	def getData(self, age=0):
		"""
		Return a collection of data a certain number of seconds into the past.
		"""
	
		if self._dbConn is None:
			self.open()
			
		# Fetch the entries that match
		if age <= 0:
			self._cursor.execute('SELECT * FROM pi2o ORDER BY dateTimeStart DESC LIMIT 4')
		else:
			# Figure out how far to look back into the database
			tNow = time.time()
			tLookback = tNow - age
			self._cursor.execute('SELECT * FROM pi2o WHERE dateTimeStart >= %i ORDER BY dateTimeStart' % tLookback)
			
		# Loop over the output rows
		output = []
		while True:
			row = self._cursor.fetchone()
			print row
			if row is None:
				break
			else:
				output.append( row )
				
		self.close()
		
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
			
		if self._dbConn is None:
			self.open()
			
		# Add the entry to the database
		if status == 'on':
			self._cursor.execute('INSERT INTO pi2o (dateTimeStart,dateTimeStop,zone,wxAdjust) VALUES (%i,%i,%i,%f)' % (timestamp, 0, zone, wxAdjustment))
		else:
			self._cursor.execute('SELECT dateTimeStart FROM pi2o WHERE zone == %i ORDER BY dateTimeStart DESC' % zone)
			row = self._cursor.fetchone()
			self._cursor.execute('UPDATE pi2o SET dateTimeStop = %i WHERE dateTimeStart == %i AND zone == %i' % (timestamp, row['dateTimeStart'], zone))
		self._dbConn.commit()
		
		self.close()
		
		return True
