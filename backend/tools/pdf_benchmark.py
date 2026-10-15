"""CPU-only renderer benchmark; no database connections, no generated documents retained.

Use development configuration with an inert localhost:1 DATABASE_URL (see report reproduction).
"""
import json
import statistics
import time
from app.modules.invoicing.pdf.document import PdfDocument, PdfLine, PdfParty, PdfSource, PdfVatRow
from app.modules.invoicing.pdf.render import render_pdf
from app.core.db import engine
from sqlalchemy import event


@event.listens_for(engine, 'do_connect')
def refuse_database(*args, **kwargs):
    raise RuntimeError('The CPU-only PDF benchmark must never connect to a database')

results=[]
for count in (10,100,500,2000):
    lines=tuple(PdfLine(position=str(i+1),description='Synthetic consulting service',unit='hour',quantity='1.000',unit_price='100.00',vat_rate='25.00',net='100.00',vat='25.00',gross='125.00',fields=()) for i in range(count))
    document=PdfDocument(number_text='1001',invoice_date='2026-10-13',due_date='2026-11-12',currency='SEK',description=None,
                         issuer=PdfParty(name='Synthetic seller AB',lines=('Test street 1','SE')),
                         customer=PdfParty(name='Synthetic buyer AB',lines=('Test street 2','SE')),
                         sources=(PdfSource(date='2026-10-13',fields=()),),lines=lines,
                         vat_rows=(PdfVatRow(rate='25.00',net=f'{count*100}.00',vat=f'{count*25}.00'),),
                         net=f'{count*100}.00',vat=f'{count*25}.00',gross=f'{count*125}.00')
    times=[]
    for _ in range(3):
        start=time.perf_counter()
        data=render_pdf(document)
        times.append(round(time.perf_counter()-start,3))
        assert data.startswith(b'%PDF-')
    results.append(dict(lines=count,seconds=times,median_seconds=statistics.median(times),bytes=len(data)))
print(json.dumps(dict(environment='Linux production backend image; CPU-only render, no database or HTTP',results=results),indent=2))
