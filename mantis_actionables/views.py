__author__ = 'Philipp Lang'

from django.shortcuts import render_to_response
from django.template import RequestContext
from django.db.models import Q

from querystring_parser import parser

import datetime

from dingos.view_classes import BasicJSONView
from dingos.models import IdentifierNameSpace

from . import DASHBOARD_CONTENTS
from .models import SingletonObservable,Source
from .mantis_import import singleton_observable_types

#init column_dicts
cols_cut = {}
cols_all = {}

#cols to display in tables (query_select_row,col_name,searchable)
COLS_TO_DISPLAY = [
    ('sources__tlp','TLP','0'),
    ('sources__timestamp','Source TS','0'),
    ('value','Value','1'),
    ('sources__top_level_iobject__identifier__namespace__uri','STIX Namespace','0'),
    ('sources__top_level_iobject__name','STIX Name','0')
]

#optinal columns to display (index,query_select_row,col_name)
OPT_COLS = [
    (2,('type__name','Type','1')),
]

def fillColDict(colsDict,cols):
    for index,col in zip(range(len(cols)),cols):
        colsDict[index] = col

fillColDict(cols_cut,COLS_TO_DISPLAY)
for index,content in OPT_COLS:
    COLS_TO_DISPLAY.insert(index,content)
fillColDict(cols_all,COLS_TO_DISPLAY)

def safe_cast(val, to_type, default=None):
    try:
        return to_type(val)
    except ValueError:
        return default

def getColumns(tableId):
    table_info = DASHBOARD_CONTENTS.get(tableId,None)
    if table_info:
        if table_info['show_type_column']:
            return cols_all
        else:
            return cols_cut
    return {}

def datatable_query(table_name, post):
    post_dict = parser.parse(str(post.urlencode()))

    # Collect prepared statement parameters in here
    params = []

    cols = getColumns(table_name)
    cols = dict((x, y[0]) for x, y in cols.items())

    # Base query
    table = DASHBOARD_CONTENTS[table_name]
    if table['basis'] == 'SingletonObservable':
        base = SingletonObservable.objects
        types = table['types']
        type_ids = []
        if isinstance(types,str):
            types = [types]
        for type in types:
            try:
                type_ids.append(singleton_observable_types[type])
            except KeyError:
                continue
        if type_ids:
            q = base.filter(type_id__in=type_ids).values_list(*(cols.values()))
            #sources__id for join on sources table
            q_count_all = base.filter(type_id__in=type_ids).values_list('sources__id').count()
        else:
            return (base.none(),0,0)

    # Treat the filter values (WHERE clause)
    col_filters = []
    for colk, colv in post_dict.get('columns', {}).iteritems():
        srch = colv.get('search', False)
        if not srch:
            continue
        srch = srch.get('value', False)

        if not srch or srch.lower()=='all':
            continue
        # srch should have a value
        col_filters.append({
            cols[colk] + '__exact' : srch
        })

    if col_filters:
        queries = [Q(**filter) for filter in col_filters]
        query = queries.pop()

        # Or the Q object with the ones remaining in the list
        for item in queries:
            query &= item

        # Query the model
        q = q.filter(query)


    col_search = []
    # The search value
    sv = post_dict.get('search', {})
    sv = str(sv.get('value', '')).strip()
    if sv != '':
        for n,c in cols.iteritems():
            if post_dict['columns'][n]['searchable'] == "true":
                col_search.append({
                    c + '__contains' : sv
                })

    if col_search:
        queries = [Q(**filter) for filter in col_search]
        query = queries.pop()

        # Or the Q object with the ones remaining in the list
        for item in queries:
            query |= item

        # Query the model
        q = q.filter(query)


    # Treat the ordering of columns
    order_cols = []
    for colk, colv in post_dict.get('order', {}).iteritems():
        scol = colv.get('column', 0)
        scol = cols.get(scol)
        if not scol:
            scol = cols[0]
        sdir = colv.get('dir')
        if sdir == 'desc':
            order_cols.append('-' + scol)
        else: #asc + fallback
            order_cols.append(scol)
    if order_cols:
        q = q.order_by(*order_cols)


    q_count_filtered = q.count()
    # Treat the paging/limit
    length = safe_cast(post_dict.get('length'), int, 10)
    if length<-1:
        length = 10
    start = safe_cast(post_dict.get('start'), int, 0)
    if start<0:
        start = 0
    if length>0:
        q = q[start:start+length]
        params.append(length)
        params.append(start)

    return (q,q_count_all,q_count_filtered)

class ActionablesTableSource(BasicJSONView):

    @property
    def returned_obj(self):
        POST = self.request.POST.copy()

        draw_val = safe_cast(POST.get('draw', 0), int, 0)
        res = {
            'draw': draw_val,
            'recordsTotal': 0,
            'recordsFiltered': 0,
            'data': [],
            'error': '',
            'cols': {}
        }

        # POST has the following parameters
        # http://www.datatables.net/manual/server-side#Configuration

        # We currently override the length to be fixed at 10
        POST[u'length'] = u'10'

        table_name = POST.get('table_type')
        if table_name in DASHBOARD_CONTENTS.keys():



            # Build the query for the data, and fetch that stuff
            q,res['recordsTotal'],res['recordsFiltered'] = datatable_query(table_name, POST)
            for row in q:
                row = list(row)
                row[0] = Source.TLP_COLOR_CSS[row[0]]
                row[1] = datetime.datetime.date(row[1]).strftime('%d-%m-%Y %X')
                res['data'].append(row)

            # Fetch the column filter values
            if draw_val == 1:
                res['cols'][table_name + '_type_filter'] = [{'all': 'All'}]
                types = DASHBOARD_CONTENTS[table_name]['types']
                if len(types) > 1:
                    for type_name in types :
                        res['cols'][table_name + '_type_filter'].append({type_name: type_name})
                res['cols'][table_name + '_tlp_filter'] = [{'all': 'All'}]
                res['cols'][table_name + '_ns_filter'] = [{'all' : 'All'}]
                namespaces = list(IdentifierNameSpace.objects.all().values_list('uri',flat=True))
                if len(namespaces) > 1:
                    for ns in namespaces:
                        res['cols'][table_name + '_ns_filter'].append({ns : ns})
        return res

def index(request):
    content_dict = {
        'title' : 'MANTIS Actionables Dashboard',
        'tables' : []
    }
    for id,table_info in DASHBOARD_CONTENTS.items():
        content_dict['tables'].append((table_info['name'],getColumns(id)))
    return render_to_response('mantis_actionables/index.html', content_dict, context_instance=RequestContext(request))