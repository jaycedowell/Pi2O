#!/bin/bash

rm -f pi2o-data.db
sqlite3 pi2o-data.db < pi2o-data.sql
