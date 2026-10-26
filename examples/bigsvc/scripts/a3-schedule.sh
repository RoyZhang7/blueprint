#!/bin/bash
# Prints a random lambda schedule "r:s,r:s,..." for the A3 random-composition run.
# usage: a3-schedule.sh [steps=40] [step_s=30] [min=100] [max=1500] [seed=7]
steps=${1:-40}; dur=${2:-30}; lo=${3:-100}; hi=${4:-1500}; seed=${5:-7}
RANDOM=$seed
out=""
for i in $(seq 1 $steps); do
  r=$(( lo + RANDOM % (hi - lo + 1) ))
  r=$(( (r / 10) * 10 ))
  out+="${out:+,}$r:$dur"
done
echo "$out"
