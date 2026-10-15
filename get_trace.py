#!/usr/bin/env python3
"""Read traces from an HP 8753D over GPIB (xyphro UsbGpib adapter, pyvisa-py).

Examples:
    python3 get_trace.py                    # active channel, formatted trace -> CSV
    python3 get_trace.py --chan 1 2         # both channels
    python3 get_trace.py --sweep            # take a fresh single sweep first
    python3 get_trace.py --s1p              # also save corrected complex data as Touchstone
    python3 get_trace.py --plot             # also save a PNG (needs matplotlib)
    python3 get_trace.py --chan 1 --format SMIC   # Smith chart: CSV + Letter-size SVG
"""
import argparse
import math
import os
import sys
import time

import pyvisa

DEFAULT_RESOURCE = 'USB0::1003::8293::HEWLETT_PACKARD_8753D_0_6.14::0::INSTR'
PARAMS = ['S11', 'S21', 'S12', 'S22', 'AR', 'BR', 'AB', 'MEASA', 'MEASB', 'MEASR']
Z0 = 50.0
FORMATS = ['LOGM', 'PHAS', 'DELA', 'SMIC', 'POLA', 'LINM', 'SWR', 'REAL', 'IMAG']


class HP8753:
    def __init__(self, resource, timeout_ms=30000):
        if sys.platform == 'win32':
            self.rm = pyvisa.ResourceManager()  # NI-VISA
        else:
            self.rm = pyvisa.ResourceManager('@py')  # pyvisa-py + libusb
        self.v = self.rm.open_resource(resource)
        self.v.timeout = timeout_ms

    def close(self):
        self.v.close()

    def q(self, cmd):
        return self.v.query(cmd).strip()

    def flag(self, cmd):
        return self.q(cmd + '?') == '1'

    def idn(self):
        return self.q('*IDN?')

    def select_channel(self, chan):
        self.v.write('CHAN%d;' % chan)

    def set_format(self, fmt):
        self.v.write('%s;' % fmt)

    def single_sweep(self):
        # OPC? makes the instrument answer only after the sweep has finished
        self.q('OPC?;SING;')

    def setup(self):
        star = float(self.q('STAR?'))
        stop = float(self.q('STOP?'))
        points = int(float(self.q('POIN?')))
        linear = self.flag('LINFREQ')
        param = next((p for p in PARAMS if self.flag(p)), '?')
        fmt = next((f for f in FORMATS if self.flag(f)), '?')
        return dict(start=star, stop=stop, points=points, linear=linear,
                    param=param, format=fmt)

    def _read_pairs(self, cmd):
        self.v.write('FORM4;')  # ASCII transfer
        raw = self.q(cmd)
        vals = [float(s) for s in raw.replace('\n', ',').split(',') if s.strip()]
        return list(zip(vals[0::2], vals[1::2]))

    def formatted(self):
        """Trace as displayed (e.g. dB for LOGM); second value used by Smith/polar."""
        return self._read_pairs('OUTPFORM;')

    def corrected(self):
        """Error-corrected complex data (real, imag), independent of display format."""
        return self._read_pairs('OUTPDATA;')


def frequencies(s, n):
    if n == 1:
        return [s['start']]
    if s['linear']:
        return [s['start'] + (s['stop'] - s['start']) * k / (n - 1) for k in range(n)]
    return [s['start'] * (s['stop'] / s['start']) ** (k / (n - 1)) for k in range(n)]


def save_csv(path, s, freqs, data):
    with open(path, 'w') as f:
        f.write('# HP 8753D %s %s, %g-%g Hz, %d points\n'
                % (s['param'], s['format'], s['start'], s['stop'], len(data)))
        if s['format'] == 'SMIC':
            # val1/val2 are the reflection coefficient; add the impedance it represents
            f.write('freq_Hz,gamma_re,gamma_im,R_ohm,X_ohm\n')
            for fr, (a, b) in zip(freqs, data):
                g = complex(a, b)
                z = Z0 * (1 + g) / (1 - g) if g != 1 else complex(math.inf, 0)
                f.write('%.9g,%.9g,%.9g,%.6g,%.6g\n' % (fr, a, b, z.real, z.imag))
            return
        f.write('freq_Hz,val1,val2\n')
        for fr, (a, b) in zip(freqs, data):
            f.write('%.9g,%.9g,%.9g\n' % (fr, a, b))


def save_s1p(path, s, freqs, data):
    with open(path, 'w') as f:
        f.write('! HP 8753D %s corrected data\n' % s['param'])
        f.write('# HZ S RI R 50\n')
        for fr, (re, im) in zip(freqs, data):
            f.write('%.9g %.9g %.9g\n' % (fr, re, im))


def save_smith_svg(path, s, freqs, data, title):
    """Smith chart of SMIC data on a US Letter page (8.5 x 11 in, 100 units per inch)."""
    W, H, R, cx, cy = 850, 1100, 340, 425, 470
    pt = lambda g: (cx + R * g.real, cy - R * g.imag)
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="8.5in" height="11in" '
           'viewBox="0 0 %d %d" font-family="Helvetica, Arial, sans-serif">' % (W, H),
           '<rect width="100%" height="100%" fill="white"/>',
           '<clipPath id="u"><circle cx="%g" cy="%g" r="%g"/></clipPath>' % (cx, cy, R),
           '<text x="%g" y="70" font-size="26" text-anchor="middle" font-weight="bold">%s</text>'
           % (cx, title),
           '<text x="%g" y="100" font-size="15" text-anchor="middle" fill="#444">'
           '%s Smith chart, %.6g-%.6g MHz, %d points, Z0 = %g ohm</text>'
           % (cx, s['param'], s['start'] / 1e6, s['stop'] / 1e6, len(data), Z0)]

    # grid: constant-resistance circles and constant-reactance arcs, clipped to |G| = 1
    grid = '<g clip-path="url(#u)" fill="none" stroke="#b8c4d0" stroke-width="0.8">'
    vals = [0.2, 0.5, 1, 2, 5]
    for r in vals:
        grid += '<circle cx="%g" cy="%g" r="%g"/>' % (cx + R * r / (1 + r), cy, R / (1 + r))
    for x in vals + [-v for v in vals]:
        grid += '<circle cx="%g" cy="%g" r="%g"/>' % (cx + R, cy - R / x, R / abs(x))
    grid += '<line x1="%g" y1="%g" x2="%g" y2="%g"/></g>' % (cx - R, cy, cx + R, cy)
    out.append(grid)
    out.append('<circle cx="%g" cy="%g" r="%g" fill="none" stroke="#567" stroke-width="1.5"/>'
               % (cx, cy, R))
    for r in vals:
        x, y = pt(complex((r - 1) / (r + 1), 0))
        out.append('<text x="%g" y="%g" font-size="11" fill="#567">%g</text>' % (x + 2, y - 3, r))
    for xv in vals + [-v for v in vals]:
        g = (1j * xv - 1) / (1j * xv + 1)
        x, y = pt(g * 1.06)
        out.append('<text x="%g" y="%g" font-size="11" fill="#567" text-anchor="middle" '
                   'dominant-baseline="middle">%+gj</text>' % (x, y, xv))

    # trace
    gam = [complex(a, b) for a, b in data]
    out.append('<polyline fill="none" stroke="#c0392b" stroke-width="2" points="%s"/>'
               % ' '.join('%.2f,%.2f' % pt(g) for g in gam))

    # markers: start, middle, stop, best match (smallest |G|)
    best = min(range(len(gam)), key=lambda k: abs(gam[k]))
    marks = [('1', 0), ('2', len(gam) // 2), ('3', len(gam) - 1)]
    if best not in [k for _, k in marks]:
        marks.append(('M', best))
    rows = []
    for label, k in marks:
        x, y = pt(gam[k])
        out.append('<circle cx="%g" cy="%g" r="4.5" fill="#1f4e79"/>' % (x, y))
        out.append('<text x="%g" y="%g" font-size="14" font-weight="bold" fill="#1f4e79">%s</text>'
                   % (x + 7, y - 7, label))
        g = gam[k]
        z = Z0 * (1 + g) / (1 - g) if g != 1 else complex(math.inf, 0)
        swr = (1 + abs(g)) / (1 - abs(g)) if abs(g) < 1 else math.inf
        rows.append((label, freqs[k] / 1e6, abs(g), math.degrees(math.atan2(g.imag, g.real)),
                     z.real, z.imag, swr))

    # marker table
    y0 = cy + R + 70
    cols = [(70, 'Mkr'), (140, 'Freq (MHz)'), (270, '|Gamma|'), (360, 'Angle'),
            (460, 'R (ohm)'), (570, 'X (ohm)'), (680, 'SWR')]
    for x, h in cols:
        out.append('<text x="%d" y="%d" font-size="14" font-weight="bold">%s</text>' % (x, y0, h))
    out.append('<line x1="65" y1="%d" x2="785" y2="%d" stroke="#333"/>' % (y0 + 8, y0 + 8))
    for i, row in enumerate(rows):
        y = y0 + 32 + i * 26
        cells = [row[0], '%.4f' % row[1], '%.4f' % row[2], '%.1f deg' % row[3],
                 '%.2f' % row[4], '%+.2f' % row[5], '%.2f' % row[6] if row[6] < 1e6 else 'inf']
        for (x, _), c in zip(cols, cells):
            out.append('<text x="%d" y="%d" font-size="14">%s</text>' % (x, y, c))
    out.append('<text x="70" y="%d" font-size="12" fill="#666">1 = start, 2 = centre, '
               '3 = stop, M = best match (smallest |Gamma|). Captured %s.</text>'
               % (y0 + 32 + len(rows) * 26 + 10, time.strftime('%Y-%m-%d %H:%M:%S')))
    out.append('</svg>')
    with open(path, 'w') as f:
        f.write('\n'.join(out))
    print('  smith ', path)


def save_plot(path, s, freqs, data):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print('  matplotlib not installed, skipping plot')
        return
    plt.figure(figsize=(9, 4.5))
    plt.plot([f / 1e6 for f in freqs], [d[0] for d in data])
    plt.xlabel('Frequency (MHz)')
    plt.ylabel(s['format'])
    plt.title('HP 8753D %s %s' % (s['param'], s['format']))
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(path, dpi=110)
    plt.close()
    print('  plot  ', path)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--resource', default=DEFAULT_RESOURCE)
    ap.add_argument('--chan', type=int, nargs='+', choices=[1, 2],
                    help='channel(s) to read; default is the active channel')
    ap.add_argument('--format', choices=FORMATS,
                    help='set the display format before reading (e.g. SMIC for Smith chart)')
    ap.add_argument('--sweep', action='store_true',
                    help='take a single sweep before reading (leaves the analyzer in HOLD)')
    ap.add_argument('--s1p', action='store_true', help='also save corrected data as .s1p')
    ap.add_argument('--plot', action='store_true', help='also save a PNG plot')
    ap.add_argument('--outdir', default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                     'vna_traces'))
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    vna = HP8753(args.resource)
    try:
        print(vna.idn())
        stamp = time.strftime('%Y%m%d_%H%M%S')
        for chan in args.chan or [None]:
            if chan:
                vna.select_channel(chan)
            if args.format:
                vna.set_format(args.format)
            if args.sweep:
                vna.single_sweep()
            s = vna.setup()
            data = vna.formatted()
            freqs = frequencies(s, len(data))
            tag = '%s_ch%s_%s_%s' % (stamp, chan or 'act', s['param'], s['format'])
            base = os.path.join(args.outdir, 'trace_' + tag)

            print('channel %s: %s %s, %g-%g Hz, %d pts'
                  % (chan or 'active', s['param'], s['format'], s['start'], s['stop'], len(data)))
            save_csv(base + '.csv', s, freqs, data)
            print('  csv   ', base + '.csv')
            if s['format'] == 'SMIC':
                save_smith_svg(base + '_smith.svg', s, freqs, data, vna.idn().replace(',', ' '))
            if args.s1p:
                save_s1p(base + '.s1p', s, freqs, vna.corrected())
                print('  s1p   ', base + '.s1p')
            if args.plot:
                save_plot(base + '.png', s, freqs, data)
    except pyvisa.errors.VisaIOError as e:
        sys.exit('GPIB error: %s\n(if it keeps timing out, replug the adapter)' % e)
    finally:
        vna.close()


if __name__ == '__main__':
    main()
