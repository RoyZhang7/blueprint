#!/usr/bin/env python3
"""Phase K4.2 step c: HarvestContainers' Balancer does not compile with -DBALANCE_INTERRUPTS as published: doHarvest
(main.c) assigns needs_rebalance, which is not declared in its scope. This adds the one local declaration under the same
#ifdef and changes nothing else: the IRQ sampling and the re-ordering of harvestCores run as written (harvestCores is not
read by the core choice, ComputeSecondaryAffinityMask walks cpuList, so no lending decision changes).
usage: irqfix.py BALANCER_SRC_DIR   (edits main.c in place; refuses if the published text is not found)
"""
import os, sys

p = os.path.join(sys.argv[1], "main.c"); s = open(p).read()
a = "    #ifdef BALANCE_INTERRUPTS\n    /* If time between last sample and this sample > IRQ_SAMPLE_RATE */\n"
b = ("    #ifdef BALANCE_INTERRUPTS\n    int needs_rebalance = 0;   /* K4.2: undeclared in the published code */\n"
     "    /* If time between last sample and this sample > IRQ_SAMPLE_RATE */\n")
if s.count(a) != 1: sys.exit("main.c: published BALANCE_INTERRUPTS block not found exactly once")
open(p, "w").write(s.replace(a, b)); print("patched main.c (needs_rebalance declared in doHarvest)")
