# -*- coding: utf-8 -*-

import sys
import os
import subprocess
import io
from workflow import Workflow, ICON_WEB, web


iterm_plist = os.path.expanduser("~/Library/Preferences/com.googlecode.iterm2.plist")


class ItermPlistError(Exception):
    pass


def create_local_copy(iterm_plist=iterm_plist):
    """
    Make a local copy of the and return the new filename.
    """
    # check if plist file exists
    if os.path.exists(iterm_plist):
        # copy to random name in tmp folder
        tmp_plist_file = "/tmp/com.googlecode.iterm2.plist"
        tmp_json_file = "/tmp/com.googlecode.iterm2.json"
        if os.path.exists(tmp_json_file):
            return tmp_json_file
        else:
            open(tmp_plist_file, 'wb').write(open(iterm_plist, 'rb').read())
            p = subprocess.Popen(["plutil", "-p", tmp_plist_file],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,shell=False)
            stdout, _ = p.communicate()
            # print(stdout)
            with io.open(tmp_json_file, 'w', encoding='utf-8') as f:
                f.write(stdout.decode('utf-8'))
            # os.remove(tmp_plist_file)
            return tmp_json_file
    else:
        raise ItermPlistError('Can not find plist file at: ' + iterm_plist)


def filter(items, query):
    if query == "":
        return items
    else:
        query = query.lower()
        new_items = []
        for item in items:
            if query in item['name'].lower() or query in item['tags'].lower():
                new_items.append(item)
        return new_items


def wsh_list(wf, query):
    tmp_json_file = create_local_copy()
    keyword_name = '"Name" => '
    keyword_tags = '"Tags" => '
    items = []
    name = ""
    tags = []
    tag_end = True
    with io.open(tmp_json_file, 'r', encoding='utf-8') as f:
        for line in f.readlines():
            if line.find(keyword_name) != -1:
                name = line.replace(keyword_name, '').replace('"', '').strip()
            if line.find(keyword_tags) != -1:
                tag_end = False
                continue
            if tag_end is False:
                if line.find(']') != -1:
                    items.append(dict(name=name, tags=' '.join(tags)))
                    name = ""
                    tags = []
                    tag_end = True
                else:
                    tags.append(line.split('=>')[1].replace('"', '').strip())

    # os.remove(tmp_json_file)
    # Loop through the returned posts and add an item for each to
    # the list of results for Alfred
    items = filter(items, query)
    for item in items:
        wf.add_item(title=item['name'],
                    subtitle=item['tags'],
                    arg=item['name'],
                    valid=True)

    # Send the results to Alfred as XML
    wf.send_feedback()


if __name__ == '__main__':
    my_wf = Workflow()
    def wsh_lists(wf):
        wsh_list(wf, query="{query}")
    sys.exit(my_wf.run(wsh_lists))