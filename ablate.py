import sys
from runner import *
import policy as P
seeds = range(0, 1000)
for name in ('go_out', 'lone_lead', 'save_high'):
    for k in P.FLAGS: P.FLAGS[k] = True
    P.FLAGS[name] = False
    r = evaluate(P.policy, seeds)
    print('OFF', name, '| L %+.3f  P %+.3f' % (r['landlord']['paired_diff'], r['peasant']['paired_diff']), flush=True)
