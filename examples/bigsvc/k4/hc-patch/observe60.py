#!/usr/bin/env python3
"""Phase K4.2 step b: the paper's 60 s Observation Phase (nsdi26-hall §3.3: "During the Observation Phase of a Primary,
harvesting is disabled (i.e., no cores are shared with the Secondary) ... set to 60 seconds in our implementation"),
compiled in only with -DOBSERVE60. Three changes to HarvestContainers' Balancer, each under #ifdef OBSERVE60:
  balancer.h  RAMPUP1 = RAMPUP2 = 30, i.e. 60 samples of IDLEACTIVE_SAMPLE_RATE = 1 s (shipped: 5 + 5);
  main.c      doAdjust is not called while the state is still OBSERVE (as shipped it runs right after the first
              doObserve and sets HARVEST, so the shipped observation lasts one sample);
  main.c      evaluatePrimaryContainer returns before doHarvest while the state is OBSERVE (as shipped, doHarvest runs
              from the first evaluation, so nothing is held back).
usage: observe60.py BALANCER_SRC_DIR   (edits main.c and balancer.h in place; refuses if the shipped text is not found)
"""
import os, sys

d = sys.argv[1]


def patch(fn, pairs):
    p = os.path.join(d, fn); s = open(p).read()
    for a, b in pairs:
        if s.count(a) != 1: sys.exit(f"{fn}: expected text not found exactly once: {a[:60]!r}")
        s = s.replace(a, b)
    open(p, "w").write(s); print("patched", fn)


patch("balancer.h", [(
    "static unsigned RAMPUP1 = 5;\nstatic unsigned RAMPUP2 = 5;\n",
    "#ifdef OBSERVE60\n/* K4.2: the paper's 60 s Observation Phase, 60 samples of IDLEACTIVE_SAMPLE_RATE = 1 s */\n"
    "static unsigned RAMPUP1 = 30;\nstatic unsigned RAMPUP2 = 30;\n#else\n"
    "static unsigned RAMPUP1 = 5;\nstatic unsigned RAMPUP2 = 5;\n#endif\n")])
patch("main.c", [
    ("        if (bi->currentState == OBSERVE) {\n            doObserve(ci, bi);\n        }\n\n        doAdjust(ci, bi);\n",
     "        if (bi->currentState == OBSERVE) {\n            doObserve(ci, bi);\n        }\n\n"
     "#ifdef OBSERVE60\n        /* K4.2: stay in the Observation Phase until doObserve ends it */\n        if (bi->currentState != OBSERVE)\n#endif\n"
     "        doAdjust(ci, bi);\n"),
    ("    result = doHarvest(ci, bi);\n",
     "#ifdef OBSERVE60\n    /* K4.2: no harvesting during the Observation Phase (paper section 3.3) */\n    if (bi->currentState == OBSERVE) return 0;\n#endif\n"
     "    result = doHarvest(ci, bi);\n"),
])
