#!/bin/bash

tag=`date +"%Y%m%d-%H%M%S"`
sqlite3 pi2o-tanks.db ".backup 'tanks-${tag}.db.bak'"
