# -*- coding: utf-8 -*-
from __future__ import print_function
import os
import json
import requests
import collections
import time
import sys

import util

# requires internet connection; time intensive
step2 = 0  # 2 for ghtorrent.csv, 1 for github.json

# doesn't require internet connection
step3 = step4 = step5 = 1

api_url = "https://api.github.com/search/repositories"
content_url = "https://raw.githubusercontent.com/"
data_dir = 'data'

def get_colorscheme_stat(vimrcs):
    colorschemes = []
    outstr = []
    for line in vimrcs:
        if line.startswith("colorscheme"):
            words = line.split(' ')
            if len(words) > 1:
                colorschemes.append(words[1])
    total = len(colorschemes)
    cs = collections.Counter(colorschemes).most_common(10)
    for n, i in enumerate(cs):
        outstr.append("%d. %s %.2f%%" % (n, i[0], i[1] * 100. / total))
    return '\n\n#Colorscheme stat\n' + '\n'.join(outstr) + '\n'

def get_stat(vimrcs, total_vimrcs):
    outstr = []
    outstr.append("Most common vim config out of " + str(total_vimrcs) + " vimrc's\n")

    eigenvimrc = collections.Counter(vimrcs).most_common(80)
    for n, i in enumerate(eigenvimrc):
        outstr.append("%d. ```%s``` %.2f%%" % (n, i[0], i[1] * 100. / total_vimrcs))
    return '\n'.join(outstr), eigenvimrc

def sanitize_line(line):
    # strip trailing whitespaces
    sanitized_line = line.strip()
    # strip whitespace surrounding '=' operator
    sanitized_line = sanitized_line.replace(" = ", "=")
    # format short-hand keyword
    sanitized_line = util.keyword_reformat(sanitized_line)
    # strip comment
    # detection is done by checking if the number of quotes
    # are odd
    if line.count('"') % 2 != 0:
        sanitized_line = sanitized_line[:sanitized_line.rfind('"')].strip()
    return sanitized_line

class pm_stat:  # pm stands for plugin manager
    def __init__(self):
        self.pms = {
            'pathogen': 0,
            'vundle': 0,
            'vam': 0,
            'neobundle': 0,
            'dein': 0,
            'plug': 0,
            'others or none': 0}

    def get_pm_type(self, text):
        if 'call pathogen#infect' in text:
            self.pms['pathogen'] += 1
        elif 'call vundle#begin' in text:
            self.pms['vundle'] += 1
        elif 'call vam#ActivateAddons' in text:
            self.pms['vam'] += 1
        elif 'call neobundle#begin' in text:
            self.pms['neobundle'] += 1
        elif 'call dein#begin' in text:
            self.pms['dein'] += 1
        elif 'call plug#begin' in text:
            self.pms['plug'] += 1
        else:
            self.pms['others or none'] += 1

    def out(self):
        total = sum(self.pms.values())
        outstr = "\n#Plugin manager stat\n\n"
        for k, v in self.pms.iteritems():
            p = v * 100. / total
            outstr += "%s: %.2f%%\n" % (k, p)
        return outstr

tic = time.time()
# step 2: scrape vimrc's
if step2:
    print("downloading vimrc's")
    if step2 is 1:
        data = json.load(open("github.json"))["items"]
    elif step2 is 2:
        data = []
        import csv
        with open("ghtorrent.csv") as csvfile:
            reader = csv.reader(csvfile, delimiter=',')
            next(reader, None)  # skip the header
            for row in reader:
                repo_name = row[0].replace("https://api.github.com/repos/", "")
                data.append({"full_name": repo_name})
    counter = 0
    offset = 0

    def save_text(repo, request):
        with open(data_dir + '/' + repo['full_name'].replace('/', ''), 'wb') \
                as f:
            f.write(request.text.encode('utf8'))

    # essentially `mkdir -p github_vimrcs`
    if not os.path.isdir(data_dir):
        os.makedirs(data_dir)

    for i in data:
        if counter > offset:
            r = requests.get(content_url + i['full_name'] + '/master/.vimrc')
            if r.status_code == 200:
                save_text(i, r)
            else:
                r2 = requests.get(content_url + i['full_name'] + '/master/vimrc')
                if r2.status_code == 200:
                    save_text(i, r2)

        counter += 1
        print("%d %.2f%%" % (counter, counter * 1. / len(data) * 100))

# step 3: process data
if step3:
    vimrcs = []
    total_vimrcs = 0
    pms = pm_stat()
    excluded_keywords = ['endfunction', 'endfunc', 'call', 'if ', 'else',
                         'endif', 'return', 'augroup', 'Bundle', 'execute',
                         '\\ }', '\\', 'endfun', 'endf', 'try', 'endtry',
                         'endwhile', 'catch', 'end', 'au!']

    filenames = os.listdir(data_dir)
    for vimrc in filenames:
        txt = open(data_dir + '/' + vimrc).read().split('\n')
        if len(txt) < 10:
            continue
        total_vimrcs += 1
        if total_vimrcs % 200 == 0:
            print("Processed %.2f%%" % (total_vimrcs * 100. / len(filenames)))
            sys.stdout.write('\033[F')

        pms.get_pm_type('\n'.join(txt))
        sanitized_txt = []
        for line in txt:
            if (not line.startswith('\"')) and \
               not any(s in line for s in excluded_keywords):
                sanitized_line = sanitize_line(line)
                # only append when line is not empty
                if len(sanitized_line) > 0:
                    sanitized_txt.append(sanitized_line)
        vimrcs.append(sanitized_txt)

    # flatten the nested list
    vimrcs = [item for sublist in vimrcs for item in sublist]

    outfile = open("README.md", "w")
    head = open("README.head.md").read()
    tail = open("README.tail.md").read()
    outstr, eigenvimrc = get_stat(vimrcs, total_vimrcs)
    outfile.write(head + outstr + get_colorscheme_stat(vimrcs) + pms.out() + tail)

# step 4: generate eigenvimrc.vim
if step4:
    if not step3:
        print("this step depends on step #3, please enable step3")
        exit()
    # essentially `mkdir -p plugin`
    if not os.path.isdir("plugin"):
        os.makedirs("plugin")
    with open('plugin/eigenvimrc.vim', 'wb') as f:
        for i in eigenvimrc:
            if i[1] * 100. / total_vimrcs >= 40:  # 40% most used
                f.write(i[0] + '\n')

# step 5: generate plot for analysis
if step5:
    if not step3:
        print("this step depends on step #3, please enable step3")
        exit()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.style.use('ggplot')
    import pylab
    y = [i[1] for i in eigenvimrc]
    x = range(1, len(y) + 1)
    pylab.scatter(x, y, c='r')
    pylab.ylabel("Number of usage")

    # power law fit
    import scipy.optimize as optimize
    logx = pylab.log10(x)
    logy = pylab.log10(y)

    def fitfunc(p, x):
        return p[0] + p[1] * x

    errfunc = lambda p, x, y: (y - fitfunc(p, x)) ** 2
    powerlaw = lambda x, amp, index: amp * (x**index)
    pinit = [1.0, -1.0]
    out = optimize.leastsq(errfunc, pinit,
                           args=(logx, logy), full_output=1)
    pfinal = out[0]
    covar = out[1]
    index = pfinal[1]
    amp = 10.0**pfinal[0]
    print(covar)

    pylab.plot(x, powerlaw(x, amp, index))
    pylab.legend(['power law fit', 'data'])
    pylab.xlim([1, x[-1]])

    pylab.savefig('fig.png')

print('elapsed: %.2f' % (time.time() - tic))
