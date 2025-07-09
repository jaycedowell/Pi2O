#!/usr/bin/env python3

import os
import sys
import numpy as np
import sqlite3


def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


def main(args):
    filename = args[0]
    
    # Load the tanks.log CSV logfile and convert the entries into SQL commands
    # for Pi2O
    data = np.loadtxt(filename, delimiter=',')
    
    # Open the Pi2O database and add the information if it doesn't already exist
    conn = sqlite3.connect('pi2o-tanks.db')
    conn.row_factory = dict_factory
    cursor = conn.cursor()
    
    # Insert the data if it doesn't already exist
    for entry in data:
        cursor.execute("SELECT * FROM tanks WHERE dateTime == %.0f" % entry[0])
        row = cursor.fetchone()
        if row is None:
            cursor.execute("INSERT INTO tanks (dateTime,usUnits,socTemp,airTemp,depth,depthErr,volume,volumeErr) VALUES (%.0f,1,%f,%f,%f,%f,%f,%f)" % tuple(entry))
            
    # Close it out
    conn.commit()
    conn.close()    


if __name__ == "__main__":
    main(sys.argv[1:])
    
