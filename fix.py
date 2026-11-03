import pipeline as pl
j = pl.load('cbf40f9f31')
try:
    pl.analyze(j)
    print('ok')
except Exception as e:
    print(e)
