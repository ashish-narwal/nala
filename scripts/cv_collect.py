import glob
import sys
from nalaf.learning.evaluators import Evaluations, Evaluation

folder = sys.argv[1]
jobid = sys.argv[2]

def n(col):
    return int(col.split(":")[1])


counts = [0] * 4
for i in range(0,4):
    counts[i] = ([0] * 5)

for fn in glob.glob(folder + "/*o{}.*".format(jobid)):    
    with open(fn) as f:
        valid = False
        for line in f.readlines():
            if line.startswith("tp:"):
                c = line.split()
                if c[-1].startswith("exact"):
                    valid = True

                    subclass = c[-2]
                    subclass = 3 if subclass == "TOTAL" else int(subclass)

                    # tp = n(c[0])
                    # fp = n(c[1])
                    # fn = n(c[2])
                    # fpo = n(c[3])
                    # fno = n(c[4])
                    for i in range(0,5):
                        counts[subclass][i] += n(c[i])

        print(fn, valid)

evaluations = Evaluations()
for subclass, c in enumerate(counts):
    subclass = "TOTAL" if subclass == 3 else subclass
    evaluations.append(Evaluation(str(subclass), "exact", *c))
for subclass, c in enumerate(counts):
    subclass = "TOTAL" if subclass == 3 else subclass
    evaluations.append(Evaluation(str(subclass), "overlapping", *c))

for e in evaluations:
    print(e)