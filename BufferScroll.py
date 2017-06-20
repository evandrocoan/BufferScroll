import sublime, sublime_plugin
from os.path import lexists, normpath, dirname
from os import makedirs, remove, rename
from hashlib import sha1
from gzip import GzipFile
from collections import OrderedDict
import _thread as thread
import threading

try:
    from cPickle import load, dump
except:
    from pickle import load, dump

import time
from os.path import basename

# import inspect
debug = {}
database = {}
Pref = {}
BufferScrollAPI = {}
db = OrderedDict()
s = {}
already_restored = {}
scroll_already_restored = {}
last_focused_view_name = ''
last_focused_goto_definition = False

g_isToAllowSelectOperationOnTheClonedView = False


def print_debug(*msg):
    if debug:
        print('BufferScroll: '+ str(msg))

def print_line():
    if debug:
        print('-'*100)

def plugin_loaded():

    global debug, database, Pref, BufferScrollAPI, db, s
    debug = False

    # open
    db = OrderedDict()
    database = dirname(sublime.packages_path())+'/Settings/BufferScroll.bin.gz'
    try:
        makedirs(dirname(database))
    except:
        pass
    try:
        gz = GzipFile(database, 'rb')
        db = load(gz);
        gz.close()
        if not isinstance(db, OrderedDict):
            db = OrderedDict(db)
    except:
        db = OrderedDict()

    # settings
    s = sublime.load_settings('BufferScroll.sublime-settings')
    Pref = Pref()
    Pref.load()
    s.clear_on_change('reload')
    s.add_on_change('reload', lambda:Pref.load())

    BufferScrollAPI = BufferScroll()
    BufferScrollAPI.init_()

    # threads listening scroll, and waiting to set data on focus change
    if not 'running_synch_data_loop' in globals():
        global running_synch_data_loop
        running_synch_data_loop = True
        thread.start_new_thread(synch_data_loop, ())

    if not 'running_synch_scroll_loop' in globals():
        global running_synch_scroll_loop
        running_synch_scroll_loop = True
        thread.start_new_thread(synch_scroll_loop, ())


def is_cloned_view( target_view ):

    views             = sublime.active_window().views()
    target_buffer_id  = target_view.buffer_id()
    views_buffers_ids = []

    for view in views:

        views_buffers_ids.append( view.buffer_id() )

    if target_buffer_id in views_buffers_ids:

        views_buffers_ids.remove( target_buffer_id )

    # print( "( fix_project_switch_restart_bug.py ) Is a cloned view: {0}".format( target_view.buffer_id() in views_buffers_ids ) )
    return target_buffer_id in views_buffers_ids


class SampleListener(sublime_plugin.EventListener):
    """
        https://github.com/evandrocoan/SublimeTextStudio/issues/26#issuecomment-262328180
    """

    def on_window_command(self, window, command, args):

        # print ("About to execute " + command)

        if command == "clone_file":

            global g_isToAllowSelectOperationOnTheClonedView
            g_isToAllowSelectOperationOnTheClonedView = True


class Pref():
    def load(self):
        global debug
        Pref.remember_color_scheme                       = s.get('remember_color_scheme', False)
        Pref.remember_syntax                             = s.get('remember_syntax', False)
        Pref.synch_bookmarks                             = s.get('synch_bookmarks', False)
        Pref.synch_marks                                 = s.get('synch_marks', False)
        Pref.synch_folds                                 = s.get('synch_folds', False)
        Pref.synch_scroll                                = s.get('synch_scroll', False)
        Pref.typewriter_scrolling                        = s.get('typewriter_scrolling', False)
        Pref.typewriter_scrolling_shift                  = int(s.get('typewriter_scrolling_shift', 0))
        Pref.typewriter_scrolling_follow_cursor_movement = s.get('typewriter_scrolling_follow_cursor_movement', True)
        Pref.use_animations                              = s.get('use_animations', False)
        Pref.i_use_cloned_views                          = s.get('i_use_cloned_views', False)
        Pref.max_database_records                        = s.get('max_database_records', 500)
        Pref.restore_scroll                              = s.get('restore_scroll', True)
        Pref.remember_settings_list                      = s.get('remember_settings_list', [])

        Pref.current_view_id                             = -1

        Pref.synch_data_running                          = False
        Pref.synch_scroll_running                        = False
        Pref.synch_scroll_last_view_id                   = 0
        Pref.synch_scroll_last_view_position             = 0
        Pref.synch_scroll_current_view_object            = None
        Pref.writing_to_disk                             = False
        version                                          = 7
        debug                                            = s.get('debug', False)
        version_current                                  = s.get('version')
        if version_current != version:
            s.set('version', version)
            sublime.save_settings('BufferScroll.sublime-settings')

    # syntax specific settings
    def get(self, type, view):
        if view.settings().has('bs_sintax'):
            syntax = view.settings().get('bs_sintax')
        else:
            syntax = view.settings().get('syntax')
            syntax = basename(syntax).split('.')[0].lower() if syntax != None else "plain text"
            view.settings().set('bs_sintax', syntax);

        # print( "Pref: " + str( Pref )  )
        # print( "Pref: " + syntax )

        if syntax and hasattr(Pref, syntax) and type in getattr(Pref, syntax):
            return getattr(Pref, syntax)[type];
        elif syntax and hasattr(Pref, syntax) and type not in getattr(Pref, syntax):
            return getattr(Pref, type)
        elif syntax and s.has(syntax):
            setattr(Pref, syntax, s.get(syntax))
            self.get(type, view);
        else:
            return getattr(Pref, type)



class BufferScrollSaveThread(threading.Thread):

    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        if not Pref.writing_to_disk:
            Pref.writing_to_disk = True

            print_line()
            # print_debug('WRITING TO DISK')
            start = time.time()

            while len(db) > Pref.max_database_records:
                db.popitem(last = False)

            gz = GzipFile(database+'.tmp', 'wb')
            dump(db, gz, -1)
            gz.close()
            try:
                remove(database)
            except:
                pass
            try:
                rename(database+'.tmp', database)
            except:
                pass

            # print_debug('time expend writting to disk', time.time()-start)
            Pref.writing_to_disk = False

class BufferScroll(sublime_plugin.EventListener):

    def init_(self):
        """
            ST BUG https://github.com/SublimeTextIssues/Core/issues/5
            the application is not sending on_load when opening/restoring a window,
            then there is this hack which will simulate or synthesize an on_load when you open the application
            since this is just a hack, there is a terrible noticeable delay, which just sucks.

            Update 2017: This is not a bug and it is how it is supposed to happen!
            https://github.com/SublimeTextIssues/Core/issues/1508

            What this does actually is create bugs when there are cloned view opened. Sublime Text
            already restore the scroll's and cursor's of the views when opening a new window or when
            first starting.
        """
        pass
        # self.on_load_async(sublime.active_window().active_view())

        # views   = None
        # windows = sublime.windows()

        # for window in windows:

        #     views = window.views()

        #     for view in views:

        #         self.on_load_async( view )


    # restore on load for new opened tabs or previews.
    def on_load_async(self, view):
        self.restore(view, 'on_load')

    # ST BUG tps://github.com/SublimeTextIssues/Core/issues/9
    def on_reload_async(self, view):
        self.restore(view, 'on_reload')

    # restore on load for cloned views
    def on_clone_async(self, view):
        # ST BUG https://github.com/SublimeTextIssues/Core/issues/8
        self.restore(sublime.active_window().active_view(), 'on_clone')

    # save data on focus lost
    def on_deactivated_async(self, view):
        global last_focused_view_name
        last_focused_view_name = view.name()+'-'+str(view.file_name())+'-'+str(view.settings().get('is_widget'))

        # ST BUG https://github.com/SublimeTextIssues/Core/issues/10
        # unable to flush when the application is closed
        # ST BUG https://github.com/SublimeTextIssues/Core/issues/181
        # the application is not sending "on_close" event when closings
        # or switching the projects, then we need to save the data on focus lost

        window = view.window();
        if not window:
            window = sublime.active_window()
        index = window.get_view_index(view)
        if index != (-1, -1): # if the view was not closed
            self.save(view, 'on_deactivated')
            self.synch_data(view)


    # track the current_view. See next event listener
    def on_activated_async(self, view):

        # global already_restored
        # window = view.window();

        # if not window:
        #     window = sublime.active_window()

        # view    = window.active_view()
        view_id = view.id()

        if not view.settings().get('is_widget'):
            Pref.current_view_id = view_id
            Pref.synch_scroll_current_view_object = view

        # if view_id not in already_restored:
        #     self.restore( view, 'on_activated', 'on_activated_async', True)

        # if view_id not in scroll_already_restored:
        #     self.restore_scroll(view)


    # save the data when background tabs are closed
    # these that don't receive "on_deactivated"
    # on_pre_close does not have a "get_view_index" ?
    def on_pre_close(self, view):
        self.save(view, 'on_pre_close')

    # save data for focused tab when saving
    def on_pre_save(self, view):
        self.save(view, 'on_pre_save')

    # typewriter scrolling
    def on_modified(self, view):
        if not view.settings().get('is_widget') and not view.is_scratch() and len(view.sel()) == 1 and Pref.get('typewriter_scrolling', view):
            # TODO STBUG if the view is in a column, for some reason the parameter view, is not correct. This fix it
            window = view.window();
            if not window:
                window = sublime.active_window()
            view = window.active_view()

            print_line()
            # print_debug('TYPEWRITER_SCROLLING')
            line, col = view.rowcol(view.sel()[0].b)
            line = line-Pref.typewriter_scrolling_shift
            if line < 1:
                line = 0
            point = view.text_point(line, col)
            position_prev = view.viewport_position() # save the horizontal scroll
            view.show_at_center(point)
            position_next = view.viewport_position() # restore the horizontal scroll
            view.set_viewport_position((position_prev[0], position_next[1]))

    # saving
    def save(self, view, where = 'unknow'):
        if view is None or not view.file_name() or view.settings().get('is_widget'):
            return

        if view.is_loading():
            sublime.set_timeout(lambda: self.save(view, where), 100)
        else:

            id, index = self.view_id(view)

            print_line()
            # print_debug ('SAVE() ')
            # print_debug ('from: '+where)
            # print_debug ('file: '+view.file_name())
            # print_debug ('id: '+id)
            # print_debug ('position: '+index)

            # creates an object for this view, if it is unknow to the package
            if id not in db:
                db[id] = {}

            # if the result of the new collected data is different
            # from the old data, then will write to disk
            # this will hold the old value for comparation
            old_db = dict(db[id])

            # if the size of the view change outside the application skip restoration
            # if not we will restore folds in funny positions, etc...
            db[id]['id'] = int(view.size())

            # scroll
            if 'l' not in db[id]:
                db[id]['l'] = {}
            # save the scroll with "index" as the id ( for cloned views )
            db[id]['l'][index] = list(view.viewport_position())
            # also save as default if no exists
            if index != '0':
                db[id]['l']['0'] = list(view.viewport_position())
            # print_debug('viewport_position: '+str(db[id]['l']['0']))

            # selections
            db[id]['s'] = [[item.a, item.b] for item in view.sel()]
            # print_debug('selections: '+str(db[id]['s']))

            # marks
            db[id]['m'] = [[item.a, item.b] for item in view.get_regions("mark")]
            # print_debug('marks: '+str(db[id]['m']))

            # bookmarks
            db[id]['b'] = [[item.a, item.b] for item in view.get_regions("bookmarks")]
            # print_debug('bookmarks: '+str(db[id]['b']))

            # previous folding save, to be able to refold
            if 'f' in db[id] and list(db[id]['f']) != []:
                db[id]['pf'] = list(db[id]['f'])

            # folding
            db[id]['f'] = [[item.a, item.b] for item in view.folded_regions()]
            # print_debug('fold: '+str(db[id]['f']))

            # color_scheme http://www.sublimetext.com/forum/viewtopic.php?p=25624#p25624
            if Pref.get('remember_color_scheme', view):
                db[id]['c'] = view.settings().get('color_scheme')
                # print_debug('color_scheme: '+str(db[id]['c']))

            # syntax
            if Pref.get('remember_syntax', view):
                db[id]['x'] = view.settings().get('syntax')
                # print_debug('syntax: '+str(db[id]['x']))

            # settings list
            settings = Pref.get('remember_settings_list', view)
            db[id]['p'] = []
            for item in settings:
                if item:
                    value = view.settings().get(item, 'waaaaaa')
                    if value != 'waaaaaa':
                        db[id]['p'].append({'k':item, 'v':value})

            # write to disk only if something changed
            if old_db != db[id] or where == 'on_deactivated':
                db.move_to_end(id)
                BufferScrollSaveThread().start()

    def view_id(self, view):
        if not view.settings().has('buffer_scroll_name'):
            view.settings().set('buffer_scroll_name', sha1(normpath(str(view.file_name()).encode('utf-8'))).hexdigest()[:8])
        return (view.settings().get('buffer_scroll_name'), self.view_index(view))

    def view_index(self, view):
        window = view.window();
        if not window:
            window = sublime.active_window()
        index = window.get_view_index(view)
        return str(window.id())+str(index)

    def restore_scroll(self, view, where = 'unknow'):

        global last_focused_view_name
        global scroll_already_restored
        global last_focused_goto_definition
        global g_isToAllowSelectOperationOnTheClonedView

        # print( "last_focused_goto_definition: " + str( last_focused_goto_definition ) )

        if last_focused_goto_definition:

            return

        if view is None \
                or not view.file_name() \
                or view.settings().get('is_widget') \
                or view.id() in scroll_already_restored \
                or view not in sublime.active_window().views():

            return

        if view.is_loading():
            sublime.set_timeout(lambda: self.restore_scroll(view, where), 100)
        else:
            scroll_already_restored[view.id()] = True

            if last_focused_view_name == '-None-None' \
                    or last_focused_view_name == 'None' \
                    or last_focused_view_name == 'Find Results-None-None' \
                    or last_focused_goto_definition \
                    or last_focused_view_name.endswith('-True'):

                pass
                # last_focused_goto_definition = False

            # Here we cannot perform the operation to restore on the just cloned view
            elif not g_isToAllowSelectOperationOnTheClonedView:
                id, index = self.view_id(view)

                print_line()
                # print_debug ('RESTORE_SCROLL()')
                # print_debug ('from: '+where)
                # print_debug ('last_focused_view_name: '+last_focused_view_name)
                # print_debug ('file: '+view.file_name())
                # print_debug ('id: '+id)
                # print_debug ('position: '+index)

                if id in db and Pref.get('restore_scroll', view):
                    # print_debug('DOING...')
                    # scroll
                    if Pref.get('i_use_cloned_views', view) and index in db[id]['l']:
                        position = tuple(db[id]['l'][index])
                        view.set_viewport_position(position, Pref.use_animations)
                    else:
                        position = tuple(db[id]['l']['0'])
                        view.set_viewport_position(position, Pref.use_animations)
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    # ugly hack
                    sublime.set_timeout(lambda: self.stupid_scroll(view, position), 50)

                    # print_debug('scroll set: '+str(position));
                    # print_debug('supposed current scroll: '+str(view.viewport_position())); # THIS LIES
                else:
                    # print_debug('SKIPPED...')
                    pass

            g_isToAllowSelectOperationOnTheClonedView = False


    def restore( self, view, where = 'unknow', isOnActaved = False ):

        global already_restored
        global last_focused_goto_definition

        # print( "last_focused_goto_definition: " + str( last_focused_goto_definition ) )

        if last_focused_goto_definition:

            return

        if view is None \
                or not view.file_name() \
                or view.settings().get('is_widget') \
                or view.id() in already_restored:

            return

        if view.is_loading():
            sublime.set_timeout(lambda: self.restore(view, where), 100)
        else:
            already_restored[view.id()] = True
            id, index = self.view_id(view)

            print_line()
            # print_debug ('RESTORE()')
            # print_debug ('from: '+where)
            # print_debug ('last_focused_view_name: '+last_focused_view_name)
            # print_debug ('file: '+view.file_name())
            # print_debug ('id: '+id)
            # print_debug ('position: '+index)

            if id in db:
                # print_debug('DOING...')

                isClonedView = False

                # if the view changed outside of the application, don't restore folds etc
                if db[id]['id'] == int(view.size()):

                    # fold
                    rs = []
                    for r in db[id]['f']:
                        rs.append(sublime.Region(int(r[0]), int(r[1])))
                    if len(rs):
                        view.fold(rs)
                        # print_debug("fold: "+str(rs));

                    global g_isToAllowSelectOperationOnTheClonedView
                    isClonedView = is_cloned_view( view )

                    # selection
                    if ( len(db[id]['s']) > 0 and not isClonedView ) or g_isToAllowSelectOperationOnTheClonedView:
                        view.sel().clear()
                        for r in db[id]['s']:
                            view.sel().add(sublime.Region(int(r[0]), int(r[1])))
                        # print_debug('selection: '+str(db[id]['s']));

                    # marks
                    rs = []
                    for r in db[id]['m']:
                        rs.append(sublime.Region(int(r[0]), int(r[1])))
                    if len(rs):
                        view.add_regions("mark", rs, "mark", "dot", sublime.HIDDEN | sublime.PERSISTENT)
                        # print_debug('marks: '+str(db[id]['m']));

                    # bookmarks
                    rs = []
                    for r in db[id]['b']:
                        rs.append(sublime.Region(int(r[0]), int(r[1])))
                    if len(rs):
                        view.add_regions("bookmarks", rs, "bookmarks", "bookmark", sublime.HIDDEN | sublime.PERSISTENT)
                        # print_debug('bookmarks: '+str(db[id]['b']));

                # color scheme
                if Pref.get('remember_color_scheme', view) and 'c' in db[id] and view.settings().get('color_scheme') != db[id]['c']:
                    view.settings().set('color_scheme', db[id]['c'])
                    # print_debug('color scheme: '+str(db[id]['c']));

                # syntax
                if Pref.get('remember_syntax', view) and 'x' in db[id] and view.settings().get('syntax') != db[id]['x'] and lexists(dirname(sublime.packages_path())+'/'+db[id]['x']):
                    view.settings().set('syntax', db[id]['x'])
                    # print_debug('syntax: '+str(db[id]['x']));

                if 'p' in db[id]:
                    for item in Pref.get('remember_settings_list', view):
                        for i in db[id]['p']:
                            if 'k' in i and i['k'] == item:
                                view.settings().set(i['k'], i['v'])
                                break

                # scroll
                if Pref.get('restore_scroll', view) and Pref.get('i_use_cloned_views', view) and index in db[id]['l']:
                    position = tuple(db[id]['l'][index])
                    view.set_viewport_position(position, Pref.use_animations)

                elif Pref.get('restore_scroll', view):
                    position = tuple(db[id]['l']['0'])
                    view.set_viewport_position(position, Pref.use_animations)

                # There is not need to an expensive and slow if, when just setting it to false is faster.
                # if g_isToAllowSelectOperationOnTheClonedView:
                g_isToAllowSelectOperationOnTheClonedView = isOnActaved or isClonedView

                # print_debug('scroll set: '+str(position));
                # print_debug('supposed current scroll: '+str(view.viewport_position())); # THIS LIES
            else:
                # print_debug('SKIPPED...')
                pass

    def stupid_scroll(self, view, position):
        view.set_viewport_position(position, Pref.use_animations)

    # def print_stupid_scroll(self, view):
        # print_debug('current scroll for: '+str(view.file_name()));
        # print_debug('current scroll: '+str(view.viewport_position()));

    def synch_data(self, view = None, where = 'unknow'):
        if view is None:
            view = Pref.synch_scroll_current_view_object

        if view is None or view.settings().get('is_widget'):
            return

        # if there is something to synch
        if not Pref.get('synch_bookmarks', view) and not Pref.get('synch_marks', view) and not Pref.get('synch_folds', view):
            return
        Pref.synch_data_running = True


        if view.is_loading():
            Pref.synch_data_running = False
            sublime.set_timeout(lambda: self.synch_data(view, where), 200)
        else:

            self.save(view, 'synch')

            # if there is clones
            clones = []
            for window in sublime.windows():
                for _view in window.views():
                    if _view.buffer_id() == view.buffer_id() and view.id() != _view.id():
                        clones.append(_view)
            if not clones:
                Pref.synch_data_running = False
                return

            print_line()
            # print_debug('SYNCH_DATA()')

            id, index = self.view_id(view)

            if Pref.get('synch_bookmarks', view):
                bookmarks = []
                for r in db[id]['b']:
                    bookmarks.append(sublime.Region(int(r[0]), int(r[1])))

            if Pref.get('synch_marks', view):
                marks = []
                for r in db[id]['m']:
                    marks.append(sublime.Region(int(r[0]), int(r[1])))

            if Pref.get('synch_folds', view):
                folds = []
                for r in db[id]['f']:
                    folds.append(sublime.Region(int(r[0]), int(r[1])))

            for _view in clones:

                # bookmarks
                if Pref.get('synch_bookmarks', _view):
                    if bookmarks:
                        if bookmarks != _view.get_regions('bookmarks'):
                            _view.erase_regions("bookmarks")
                            # print_debug ('synching bookmarks')
                            _view.add_regions("bookmarks", bookmarks, "bookmarks", "bookmark", sublime.HIDDEN | sublime.PERSISTENT)
                        else:
                            # print_debug ('skipping synch of bookmarks these are equal')
                            pass
                    else:
                        _view.erase_regions("bookmarks")

                # marks
                if Pref.get('synch_marks', _view):
                    if marks:
                        if marks != _view.get_regions('mark'):
                            _view.erase_regions("mark")
                            # print_debug ('synching marks')
                            _view.add_regions("mark", marks, "mark", "dot", sublime.HIDDEN | sublime.PERSISTENT)
                        else:
                            # print_debug ('skipping synch of marks these are equal')
                            pass
                    else:
                        _view.erase_regions("mark")

                # folds
                if Pref.get('synch_folds', _view):
                    if folds:
                        if folds != _view.folded_regions():
                            # print_debug ('synching folds')
                            _view.unfold(sublime.Region(0, _view.size()))
                            _view.fold(folds)
                        else:
                            # print_debug ('skipping synch of folds these are equal')
                            pass
                    else:
                        _view.unfold(sublime.Region(0, _view.size()))

        Pref.synch_data_running = False

    def synch_scroll(self):

        Pref.synch_scroll_running = True

        # find current view
        view = Pref.synch_scroll_current_view_object
        if view is None or view.is_loading() or not Pref.get('synch_scroll', view):
            Pref.synch_scroll_running = False
            return

        # if something changed
        if Pref.synch_scroll_last_view_id != Pref.current_view_id:
            Pref.synch_scroll_last_view_id = Pref.current_view_id
            Pref.synch_scroll_last_view_position = 0
        last_view_position = str([view.visible_region(), view.viewport_position(), view.viewport_extent()])
        if Pref.synch_scroll_last_view_position == last_view_position:
            Pref.synch_scroll_running = False
            return
        Pref.synch_scroll_last_view_position = last_view_position

        # if there is clones
        clones = {}
        clones_positions = []
        for window in sublime.windows():
            for _view in window.views():
                if not _view.is_loading() and _view.buffer_id() == view.buffer_id() and view.id() != _view.id():
                    id, index = self.view_id(_view)
                    clones[index] = _view
                    clones_positions.append(index)
        if not clones_positions:
            Pref.synch_scroll_running = False
            return

        print_line()
        # print_debug('SYNCH_SCROLL()')

        # current view
        id, index = self.view_id(view)

        # append current view to list of clones
        clones[index] = view
        clones_positions.append(index)
        clones_positions.sort()

        # find current view index
        i = [i for i,x in enumerate(clones_positions) if x == index][0]

        lenght = len(clones_positions)
        line     = view.line_height()
        # synch scroll for views to the left
        b = i-1
        previous_view = view
        while b > -1:
            current_view = clones[clones_positions[b]]
            ppl, ppt = current_view.text_to_layout(previous_view.line(previous_view.visible_region().a).b)
            cpw, cph = current_view.viewport_extent()
            left, old_top = current_view.viewport_position()
            top = ((ppt-cph)+line)
            if abs(old_top-top) >= line:
                current_view.set_viewport_position((left, top), Pref.use_animations)
            previous_view = current_view
            b -= 1

        # synch scroll for views to the right
        i += 1
        previous_view = view
        while i < lenght:
            current_view = clones[clones_positions[i]]
            top = current_view.text_to_layout(previous_view.line(previous_view.visible_region().b).a)
            left, old_top = current_view.viewport_position()
            top = top[1]-3 # 3 is the approximated height of the shadow of the tabbar. Removing the shadow Makes the text more readable
            if abs(old_top-top) >= line:
                current_view.set_viewport_position((left, top), Pref.use_animations)
            previous_view = current_view
            i += 1

        Pref.synch_scroll_running = False

class BufferScrollForget(sublime_plugin.ApplicationCommand):
    def run(self, what):
        if what == 'color_scheme':
            sublime.active_window().active_view().settings().erase('color_scheme')

class BufferScrollReFold(sublime_plugin.WindowCommand):
    def run(self):
        view = sublime.active_window().active_view()
        if view is not None:
            id, index = BufferScrollAPI.view_id(view)
            if id in db:
                if 'pf' in db[id]:
                    rs = []
                    for r in db[id]['pf']:
                        rs.append(sublime.Region(int(r[0]), int(r[1])))
                    if len(rs):
                        view.fold(rs)

                    # update the minimap
                    position = view.viewport_position()
                    view.set_viewport_position((position[0]-1,position[1]-1), Pref.use_animations)
                    view.set_viewport_position(position, Pref.use_animations)

    def is_enabled(self):
        view = sublime.active_window().active_view()
        if view is not None and view.file_name():
            id, index = BufferScrollAPI.view_id(view)
            if id in db:
                if 'pf' in db[id] and len(db[id]['pf']):
                    return True
        return False

class BufferScrollFoldSelectFolded(sublime_plugin.WindowCommand):
    def run(self):
        view = sublime.active_window().active_view()
        if view is not None:
            folds = [[item.a, item.b] for item in view.folded_regions()]
            if folds:
                view.sel().clear()
                for fold in folds:
                    view.sel().add(sublime.Region(int(fold[0]), int(fold[1])))

class BufferScrollFoldSelectUnfolded(sublime_plugin.WindowCommand):
    def run(self):
        view = sublime.active_window().active_view()
        folds = [[item.a, item.b] for item in view.folded_regions()]
        if folds:
            view.sel().clear()
            prev = 0
            for fold in folds:
                view.sel().add(sublime.Region(prev, int(fold[0])))
                if view.substr(fold[1]) == "\n":
                    prev = int(fold[1]) + 1
                else:
                    prev = int(fold[1])
            view.sel().add(sublime.Region(prev, view.size()))

def synch_scroll_loop():
    synch_scroll = BufferScrollAPI.synch_scroll
    while True:
        if not Pref.synch_scroll_running:
            Pref.synch_scroll_running = True
            sublime.set_timeout(lambda:synch_scroll(), 0)
        time.sleep(0.08)



def synch_data_loop():
    synch_data = BufferScrollAPI.synch_data
    while True:
        if not Pref.synch_data_running:
            sublime.set_timeout(lambda:synch_data(None, 'thread'), 0)
        time.sleep(0.5)



def unlockTheScrollRestoring():

    global last_focused_goto_definition
    last_focused_goto_definition = False



class BufferScrollListener(sublime_plugin.EventListener):

    definition_view     = None
    pre_definition_view = None

    isFindResultsView   = False

    def on_deactivated( self, view ):
        """
            The forwards on_deactivated(2) and on_deactivated_async(2) are used to allow the silent
            go to inside a `Find Results` view. It keep us from restoring the last scroll on the opened
            file, overriding the line we are jumping from the `Find Results` to a search result file.

            This works because the on_deactivated_async(2) is called a little latter than  on_deactivated(2).
            Therefore when we are leaving the `Find Results` view, we may correctly set the state for
            `last_focused_goto_definition` enabling and disabling it respectively.
        """

        if self.isFindResultsView:
            global last_focused_goto_definition
            last_focused_goto_definition = True

    def on_deactivated_async( self, view ):
        global last_focused_goto_definition

        self.isFindResultsView       = False
        last_focused_goto_definition = False

    def on_activated_async(self, view):
        """
            It is possible to click outside the goto definition input box and the quick panel
            stays visible, so this won't work in that case, and on_close isn't fired when the
            quick panel really is closed. If you just care about when the user is typing in this
            view, it should be enough to ignore the quick panel view in your plugin because the
            id doesn't seem to get reused. But, I'm not sure how to tell when it has been closed,
            without making changes to Packages/Default/symbols.py which I wouldn't recommend

            https://forum.sublimetext.com/t/how-to-detect-when-the-user-closed-the-goto-definition-box/25800
        """

        if self.is_find_results_view( view ):
            self.isFindResultsView = True

        if self.pre_definition_view is not None:

            # print('this view is the goto_definition input view', view.id(), 'activated from view', self.pre_definition_view)
            self.definition_view = view.id()
            self.pre_definition_view = None

        elif self.definition_view is not None and self.definition_view != view.id():

            # print('the goto_definition input view was just deactivated')
            self.definition_view = None

            global last_focused_goto_definition
            last_focused_goto_definition = False

    def is_find_results_view( self, view ):
        return view.name() == ("Find Results")

    def on_text_command(self, view, command_name, args):

        global last_focused_view_name
        global last_focused_goto_definition

        if command_name in ( 'goto_definition', 'navigate_to_definition', 'context_goto_definition' ):

            self.pre_definition_view = view.window().active_view().id()

            last_focused_view_name = 'None'
            last_focused_goto_definition = True

            sublime.set_timeout( unlockTheScrollRestoring, 10000 )

    def on_window_command(self, window, command_name, args):

        global last_focused_view_name
        global last_focused_goto_definition

        if command_name in ( 'goto_definition', 'navigate_to_definition', 'context_goto_definition' ):

            last_focused_view_name = 'None'
            last_focused_goto_definition = True

            sublime.set_timeout( unlockTheScrollRestoring, 10000 )

    def on_post_text_command(self, view, command_name, args):

        # typewriter_scrolling
        if (command_name == 'move' or  command_name == 'move_to') \
                and Pref.get('typewriter_scrolling_follow_cursor_movement', view):

            BufferScrollAPI.on_modified(view)

    # def on_post_window_command(self, window, command_name, args):
    #     pass





