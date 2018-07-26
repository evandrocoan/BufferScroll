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

import os
import sys
import time
from os.path import basename


# Import the debugger
from debug_tools import getLogger

# Enable debug messages: (bitwise)
#
# 0   - Disabled debugging.
# 1   - Basic logging messages.
# 2   - Original levels from tito
#
# Debugger settings: 0 - disabled, 127 - enabled
log = getLogger( 127, __name__ )

#log.setup( "Debug.txt" )
#log.clear()

# log( 1, "Debugging" )
# log( 1, "..." )
# log( 1, "..." )

# import inspect
database = ""
preferences = None
BufferScrollAPI = None

data_base = OrderedDict()
g_settings = None
already_restored = {}
scroll_already_restored = {}

last_focused_view_name = ''
disable_scroll_restoring = False
g_isToAllowSelectOperationOnTheClonedView = False


def plugin_loaded():
    global database, preferences, BufferScrollAPI, data_base, g_settings

    # open
    database = dirname(sublime.packages_path())+'/Settings/BufferScroll.bin.gz'

    try:
        makedirs(dirname(database))

    except:
        pass

    try:
        gz = GzipFile(database, 'rb')
        data_base = load(gz);
        gz.close()

        if not isinstance(data_base, OrderedDict):
            data_base = OrderedDict(data_base)
    except:
        data_base = OrderedDict()

    # settings
    g_settings = sublime.load_settings('BufferScroll.sublime-settings')
    preferences = Preferences()
    preferences.load()

    g_settings.clear_on_change('BufferScroll')
    g_settings.add_on_change('BufferScroll', lambda:preferences.load())

    BufferScrollAPI = BufferScroll()

    # threads listening scroll, and waiting to set data on focus change
    if not 'running_synch_data_loop' in globals():
        global running_synch_data_loop

        running_synch_data_loop = True
        thread.start_new_thread(synch_data_loop, ())

    if not 'running_synch_scroll_loop' in globals():
        global running_synch_scroll_loop

        running_synch_scroll_loop = True
        thread.start_new_thread(synch_scroll_loop, ())


def plugin_unloaded():
    g_settings.clear_on_change('BufferScroll')


def is_cloned_view( target_view ):
    views             = sublime.active_window().views()
    target_buffer_id  = target_view.buffer_id()
    views_buffers_ids = []

    for view in views:
        views_buffers_ids.append( view.buffer_id() )

    if target_buffer_id in views_buffers_ids:
        views_buffers_ids.remove( target_buffer_id )

    # log( 1, "( fix_project_switch_restart_bug.py ) Is a cloned view: {0}".format( target_view.buffer_id() in views_buffers_ids ) )
    return target_buffer_id in views_buffers_ids


class Preferences():
    @classmethod
    def load(cls):
        cls.remember_color_scheme                       = g_settings.get('remember_color_scheme', False)
        cls.remember_syntax                             = g_settings.get('remember_syntax', False)
        cls.synch_bookmarks                             = g_settings.get('synch_bookmarks', False)
        cls.synch_marks                                 = g_settings.get('synch_marks', False)
        cls.synch_folds                                 = g_settings.get('synch_folds', False)
        cls.synch_scroll                                = g_settings.get('synch_scroll', False)
        cls.typewriter_scrolling                        = g_settings.get('typewriter_scrolling', False)
        cls.typewriter_scrolling_shift                  = int(g_settings.get('typewriter_scrolling_shift', 0))
        cls.typewriter_scrolling_follow_cursor_movement = g_settings.get('typewriter_scrolling_follow_cursor_movement', True)
        cls.use_animations                              = g_settings.get('use_animations', False)
        cls.i_use_cloned_views                          = g_settings.get('i_use_cloned_views', False)
        cls.max_database_records                        = g_settings.get('max_database_records', 500)
        cls.restore_scroll                              = g_settings.get('restore_scroll', True)
        cls.remember_settings_list                      = g_settings.get('remember_settings_list', [])

        cls.current_view_id                             = -1

        cls.synch_data_running                          = False
        cls.synch_scroll_running                        = False
        cls.synch_scroll_last_view_id                   = 0
        cls.synch_scroll_last_view_position             = 0
        cls.synch_scroll_current_view_object            = None
        cls.writing_to_disk                             = False

    # syntax specific settings
    @classmethod
    def get(cls, type, view):
        if view.settings().has('bs_sintax'):
            syntax = view.settings().get('bs_sintax')
        else:
            syntax = view.settings().get('syntax')
            syntax = basename(syntax).split('.')[0].lower() if syntax != None else "plain text"
            view.settings().set('bs_sintax', syntax);

        # log( 1, "preferences: " + str( preferences ) )
        # log( 1, "preferences: " + syntax )

        if syntax and hasattr(preferences, syntax) and type in getattr(preferences, syntax):
            return getattr(preferences, syntax)[type];

        elif syntax and hasattr(preferences, syntax) and type not in getattr(preferences, syntax):
            return getattr(preferences, type)

        elif syntax and g_settings.has(syntax):
            setattr(preferences, syntax, g_settings.get(syntax))
            cls.get(type, view);

        else:
            return getattr(preferences, type)



class BufferScrollSaveThread(threading.Thread):

    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):

        if not preferences.writing_to_disk:
            preferences.writing_to_disk = True

            # log( 2, "" )
            # log( 2, 'WRITING TO DISK' )
            start = time.time()

            while len(data_base) > preferences.max_database_records:
                data_base.popitem(last = False)

            gz = GzipFile(database+'.tmp', 'wb')
            dump(data_base, gz, -1)
            gz.close()

            try:
                remove(database)

            except:
                pass

            try:
                rename(database+'.tmp', database)

            except:
                pass

            # log( 2, 'time expend writting to disk', time.time()-start )
            preferences.writing_to_disk = False

class BufferScroll(sublime_plugin.EventListener):

    def on_load_async(self, view):
        """
            Restore on load for new opened tabs or previews.

            When the view is opened synchronously, check if the caret/curso is on the first line
            position. If it is, then we can restore the scroll and caret/cursor positions.
            Otherwise, it is some fancy feature as Go To Symbol or a file which was opened with the
            command line with `subl file.txt 100:9` (Line 100, Column 9).
        """
        is_allowed = self._scroll_restoring_allowed( view )
        # log( 1, "is_allowed: %s", is_allowed )

        if is_allowed:
            self.restore(view, 'on_load')

        else:
            global disable_scroll_restoring

            sublime.set_timeout( unlockTheScrollRestoring, 3000 )
            disable_scroll_restoring = True

    def _scroll_restoring_allowed(self, view):
        """
            Returns True when the view is allowed to be restored by its caret and scroll position,
            False otherwise.
        """
        selection = view.sel()

        if len( selection ):
            # log( 1, "selection[0]: %s", selection[0] )
            return selection[0].end() < 1

        return True

    def on_window_command(self, window, command, args):
        """
            Finish the package: Fix Project Switch-Restart Bug
            https://github.com/evandrocoan/SublimeTextStudio/issues/26#issuecomment-262328180
        """
        # log( 1, "About to execute " + command )

        if command == "clone_file":
            global g_isToAllowSelectOperationOnTheClonedView
            g_isToAllowSelectOperationOnTheClonedView = True

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

    def on_activated_async(self, view):
        """
            Track the current_view. See next event listener.
        """
        # global already_restored
        # window = view.window();

        # if not window:
        #     window = sublime.active_window()

        # view    = window.active_view()
        view_id = view.id()

        if not view.settings().get('is_widget'):
            preferences.current_view_id = view_id
            preferences.synch_scroll_current_view_object = view

        # if view_id not in already_restored:
        #     self.restore( view, 'on_activated', 'on_activated_async', True)

        # if view_id not in scroll_already_restored:
        #     self.restore_scrolling(view)

    def on_pre_close(self, view):
        """
            Save the data when background tabs are closed
            these that don't receive "on_deactivated"
            on_pre_close does not have a "get_view_index" ?
        """
        # print("on_pre_close")
        self.save(view, 'on_pre_close')

    def on_pre_save(self, view):
        """
            Save data for focused tab when saving.
        """
        self.save(view, 'on_pre_save')

    def on_post_text_command(self, view, command_name, args):
        """
            Typewriter_scrolling
        """

        if (command_name == 'move' or  command_name == 'move_to') \
                and preferences.get('typewriter_scrolling_follow_cursor_movement', view):

            BufferScrollAPI.on_modified(view)

    def on_modified(self, view):
        """
        Typewriter scrolling.

        There is a error which could pop some times directly to this line.

        Traceback (most recent call last):
          File "D:/SublimeText/sublime_plugin.py", line 389, in run_callback
            expr()
          File "D:/SublimeText/sublime_plugin.py", line 488, in <lambda>
            run_callback('on_modified', callback, lambda: callback.on_modified(v))
          File "D:/SublimeText/Data/Packages/BufferScroll/BufferScroll.py", line 304, in on_modified
            if not view.settings().get('is_widget') and not view.is_scratch() and len(view.sel()) == 1 and preferences.get('typewriter_scrolling', view):
        TypeError: get() missing 1 required positional argument: 'view'

        Issue: https://github.com/evandrocoan/SublimeTextStudio/issues/49
        """

        # TODO STBUG if the view is in a column, for some reason the parameter view, is not correct. This fix it
        if not view.settings().get('is_widget') \
                and not view.is_scratch() \
                and len(view.sel()) == 1 \
                and preferences.get('typewriter_scrolling', view):
            window = view.window();

            if not window:
                window = sublime.active_window()

            view = window.active_view()

            # log( 2, "" )
            # log( 2, 'TYPEWRITER_SCROLLING' )
            line, col = view.rowcol(view.sel()[0].b)
            line = line-preferences.typewriter_scrolling_shift

            if line < 1:
                line = 0

            point = view.text_point(line, col)
            position_prev = view.viewport_position() # save the horizontal scroll

            view.show_at_center(point)
            position_next = view.viewport_position() # restore the horizontal scroll
            view.set_viewport_position((position_prev[0], position_next[1]))

    def save(self, view, where = 'unknow'):
        """
            saving
        """

        if view is None or not view.file_name() or view.settings().get('is_widget'):
            return

        if view.is_loading():
            sublime.set_timeout(lambda: self.save(view, where), 100)

        else:
            id, index = self.view_id(view)

            # log( 2, "" )
            # log( 2, 'SAVE() ' )
            # log( 2, 'from: '+where )
            # log( 2, 'file: '+view.file_name() )
            # log( 2, 'id: '+id )
            # log( 2, 'position: '+index )

            # creates an object for this view, if it is unknow to the package
            if id not in data_base:
                data_base[id] = {}

            # if the result of the new collected data is different
            # from the old data, then will write to disk
            # this will hold the old value for comparation
            old_db = dict(data_base[id])

            # if the size of the view change outside the application skip restoration
            # if not we will restore folds in funny positions, etc...
            data_base[id]['id'] = int(view.size())

            # scroll
            if 'l' not in data_base[id]:
                data_base[id]['l'] = {}

            # save the scroll with "index" as the id ( for cloned views )
            data_base[id]['l'][index] = list(view.viewport_position())
            # also save as default if no exists
            if index != '0':
                data_base[id]['l']['0'] = list(view.viewport_position())
            # log( 2, 'viewport_position: '+str(data_base[id]['l']['0']) )

            # selections
            data_base[id]['s'] = [[item.a, item.b] for item in view.sel()]
            # log( 2, 'selections: '+str(data_base[id]['s']) )

            # marks
            data_base[id]['m'] = [[item.a, item.b] for item in view.get_regions("mark")]
            # log( 2, 'marks: '+str(data_base[id]['m']) )

            # bookmarks
            data_base[id]['b'] = [[item.a, item.b] for item in view.get_regions("bookmarks")]
            # log( 2, 'bookmarks: '+str(data_base[id]['b']) )

            # previous folding save, to be able to refold
            if 'f' in data_base[id] and list(data_base[id]['f']) != []:
                data_base[id]['pf'] = list(data_base[id]['f'])

            # folding
            data_base[id]['f'] = [[item.a, item.b] for item in view.folded_regions()]
            # log( 2, 'fold: '+str(data_base[id]['f']) )

            # color_scheme http://www.sublimetext.com/forum/viewtopic.php?p=25624#p25624
            if preferences.get('remember_color_scheme', view):
                data_base[id]['c'] = view.settings().get('color_scheme')
                # log( 2, 'color_scheme: '+str(data_base[id]['c']) )

            # syntax
            if preferences.get('remember_syntax', view):
                data_base[id]['x'] = view.settings().get('syntax')
                # log( 2, 'syntax: '+str(data_base[id]['x']) )

            # settings list
            settings = preferences.get('remember_settings_list', view)
            data_base[id]['p'] = []

            for item in settings:

                if item:
                    value = view.settings().get(item, 'waaaaaa')

                    if value != 'waaaaaa':
                        data_base[id]['p'].append({'k':item, 'v':value})

            # write to disk only if something changed
            if old_db != data_base[id] or where == 'on_deactivated':
                data_base.move_to_end(id)
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

    def restore_scrolling(self, view, where = 'unknow'):
        global scroll_already_restored
        global disable_scroll_restoring

        # log( 1, "on restore_scrolling, disable_scroll_restoring: " + str( disable_scroll_restoring ) )
        if disable_scroll_restoring:
            return

        if view is None \
                or not view.file_name() \
                or view.settings().get('is_widget') \
                or view.id() in scroll_already_restored \
                or view not in sublime.active_window().views():

            return

        if view.is_loading():
            sublime.set_timeout(lambda: self.restore_scrolling(view, where), 100)

        else:
            scroll_already_restored[view.id()] = True

            global last_focused_view_name
            global g_isToAllowSelectOperationOnTheClonedView

            # Here we cannot perform the operation to restore on the just cloned view
            if not g_isToAllowSelectOperationOnTheClonedView:
                id, index = self.view_id(view)

                # log( 2, "" )
                # log( 2, 'RESTORE_SCROLLING()' )
                # log( 2, 'from: '+where )
                # log( 2, 'last_focused_view_name: '+last_focused_view_name )
                # log( 2, 'file: '+view.file_name() )
                # log( 2, 'id: '+id )
                # log( 2, 'position: '+index )

                if id in data_base and preferences.get('restore_scroll', view):

                    # log( 2, 'DOING...' )
                    # scroll
                    if preferences.get('i_use_cloned_views', view) and index in data_base[id]['l']:
                        position = tuple(data_base[id]['l'][index])
                        view.set_viewport_position(position, preferences.use_animations)

                    else:
                        position = tuple(data_base[id]['l']['0'])
                        view.set_viewport_position(position, preferences.use_animations)

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

                    # log( 2, 'scroll set: '+str(position)) ;
                    # log( 2, 'supposed current scroll: '+str(view.viewport_position())); # THIS LIE S
                else:
                    # log( 2, 'SKIPPED...' )
                    pass

            g_isToAllowSelectOperationOnTheClonedView = False

    def restore( self, view, where = 'unknow', isOnActaved = False ):
        global already_restored
        global disable_scroll_restoring

        # log( 1, "on restore, disable_scroll_restoring: " + str( disable_scroll_restoring ) )
        if disable_scroll_restoring:
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

            # log( 2, "" )
            # log( 2, 'RESTORE()' )
            # log( 2, 'from: '+where )
            # log( 2, 'last_focused_view_name: '+last_focused_view_name )
            # log( 2, 'file: '+view.file_name() )
            # log( 2, 'id: '+id )
            # log( 2, 'position: '+index )

            if id in data_base:
                # log( 2, 'DOING...' )
                isClonedView = False

                # if the view changed outside of the application, don't restore folds etc
                if data_base[id]['id'] == int(view.size()):
                    # fold
                    rs = []

                    for r in data_base[id]['f']:
                        rs.append(sublime.Region(int(r[0]), int(r[1])))

                    if len(rs):
                        view.fold(rs)
                        # log( 2, "fold: "+str(rs)) ;

                    global g_isToAllowSelectOperationOnTheClonedView
                    isClonedView = is_cloned_view( view )

                    # selection
                    if ( len(data_base[id]['s']) > 0 and not isClonedView ) or g_isToAllowSelectOperationOnTheClonedView:
                        view.sel().clear()
                        for r in data_base[id]['s']:
                            view.sel().add(sublime.Region(int(r[0]), int(r[1])))
                        # log( 2, 'selection: '+str(data_base[id]['s'])) ;

                    # marks
                    rs = []

                    for r in data_base[id]['m']:
                        rs.append(sublime.Region(int(r[0]), int(r[1])))

                    if len(rs):
                        view.add_regions("mark", rs, "mark", "dot", sublime.HIDDEN | sublime.PERSISTENT)
                        # log( 2, 'marks: '+str(data_base[id]['m'])) ;

                    # bookmarks
                    rs = []

                    for r in data_base[id]['b']:
                        rs.append(sublime.Region(int(r[0]), int(r[1])))

                    if len(rs):
                        view.add_regions("bookmarks", rs, "bookmarks", "bookmark", sublime.HIDDEN | sublime.PERSISTENT)
                        # log( 2, 'bookmarks: '+str(data_base[id]['b'])) ;

                # color scheme
                if preferences.get('remember_color_scheme', view) and 'c' in data_base[id] and view.settings().get('color_scheme') != data_base[id]['c']:
                    view.settings().set('color_scheme', data_base[id]['c'])
                    # log( 2, 'color scheme: '+str(data_base[id]['c'])) ;

                # syntax
                if preferences.get('remember_syntax', view) and 'x' in data_base[id] and view.settings().get('syntax') != data_base[id]['x'] and lexists(dirname(sublime.packages_path())+'/'+data_base[id]['x']):

                    view.settings().set('syntax', data_base[id]['x'])
                    # log( 2, 'syntax: '+str(data_base[id]['x'])) ;

                if 'p' in data_base[id]:

                    for item in preferences.get('remember_settings_list', view):

                        for i in data_base[id]['p']:

                            if 'k' in i and i['k'] == item:
                                view.settings().set(i['k'], i['v'])
                                break

                # scroll
                if preferences.get('restore_scroll', view) and preferences.get('i_use_cloned_views', view) and index in data_base[id]['l']:
                    position = tuple(data_base[id]['l'][index])
                    view.set_viewport_position(position, preferences.use_animations)

                elif preferences.get('restore_scroll', view):
                    position = tuple(data_base[id]['l']['0'])
                    view.set_viewport_position(position, preferences.use_animations)

                # There is not need to an expensive and slow if, when just setting it to false is faster.
                # if g_isToAllowSelectOperationOnTheClonedView:
                g_isToAllowSelectOperationOnTheClonedView = isOnActaved or isClonedView

                # log( 2, 'scroll set: '+str(position)) ;
                # log( 2, 'supposed current scroll: '+str(view.viewport_position())); # THIS LIE S
            else:
                # log( 2, 'SKIPPED...' )
                pass

    def stupid_scroll(self, view, position):
        view.set_viewport_position(position, preferences.use_animations)

    # def print_stupid_scroll(self, view):
        # log( 2, 'current scroll for: '+str(view.file_name())) ;
        # log( 2, 'current scroll: '+str(view.viewport_position())) ;

    def synch_data(self, view = None, where = 'unknow'):
        if view is None:
            view = preferences.synch_scroll_current_view_object

        if view is None or view.settings().get('is_widget'):
            return

        # if there is something to synch
        if not preferences.get('synch_bookmarks', view) and not preferences.get('synch_marks', view) and not preferences.get('synch_folds', view):
            return

        preferences.synch_data_running = True

        if view.is_loading():
            preferences.synch_data_running = False
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
                preferences.synch_data_running = False
                return

            # log( 2, "" )
            # log( 2, 'SYNCH_DATA()' )
            id, index = self.view_id(view)

            if preferences.get('synch_bookmarks', view):
                bookmarks = []

                for r in data_base[id]['b']:
                    bookmarks.append(sublime.Region(int(r[0]), int(r[1])))

            if preferences.get('synch_marks', view):
                marks = []

                for r in data_base[id]['m']:
                    marks.append(sublime.Region(int(r[0]), int(r[1])))

            if preferences.get('synch_folds', view):
                folds = []

                for r in data_base[id]['f']:
                    folds.append(sublime.Region(int(r[0]), int(r[1])))

            for _view in clones:
                # bookmarks
                if preferences.get('synch_bookmarks', _view):

                    if bookmarks:

                        if bookmarks != _view.get_regions('bookmarks'):
                            _view.erase_regions("bookmarks")
                            # log( 2, 'synching bookmarks' )
                            _view.add_regions("bookmarks", bookmarks, "bookmarks", "bookmark", sublime.HIDDEN | sublime.PERSISTENT)

                        else:
                            # log( 2, 'skipping synch of bookmarks these are equal' )
                            pass

                    else:
                        _view.erase_regions("bookmarks")

                # marks
                if preferences.get('synch_marks', _view):

                    if marks:

                        if marks != _view.get_regions('mark'):
                            _view.erase_regions("mark")
                            # log( 2, 'synching marks' )
                            _view.add_regions("mark", marks, "mark", "dot", sublime.HIDDEN | sublime.PERSISTENT)

                        else:
                            # log( 2, 'skipping synch of marks these are equal' )
                            pass

                    else:
                        _view.erase_regions("mark")

                # folds
                if preferences.get('synch_folds', _view):

                    if folds:

                        if folds != _view.folded_regions():
                            # log( 2, 'synching folds' )
                            _view.unfold(sublime.Region(0, _view.size()))
                            _view.fold(folds)

                        else:
                            # log( 2, 'skipping synch of folds these are equal' )
                            pass

                    else:
                        _view.unfold(sublime.Region(0, _view.size()))

        preferences.synch_data_running = False

    def synch_scroll(self):
        preferences.synch_scroll_running = True

        # find current view
        view = preferences.synch_scroll_current_view_object
        if view is None or view.is_loading() or not preferences.get('synch_scroll', view):
            preferences.synch_scroll_running = False
            return

        # if something changed
        if preferences.synch_scroll_last_view_id != preferences.current_view_id:
            preferences.synch_scroll_last_view_id = preferences.current_view_id
            preferences.synch_scroll_last_view_position = 0

        last_view_position = str([view.visible_region(), view.viewport_position(), view.viewport_extent()])

        if preferences.synch_scroll_last_view_position == last_view_position:
            preferences.synch_scroll_running = False
            return

        preferences.synch_scroll_last_view_position = last_view_position

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
            preferences.synch_scroll_running = False
            return

        # log( 2, "" )
        # log( 2, 'SYNCH_SCROLL()' )

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
                current_view.set_viewport_position((left, top), preferences.use_animations)
            previous_view = current_view
            b -= 1

        # synch scroll for views to the right
        i += 1
        previous_view = view

        while i < lenght:
            current_view = clones[clones_positions[i]]
            top = current_view.text_to_layout(previous_view.line(previous_view.visible_region().b).a)
            left, old_top = current_view.viewport_position()

            # 3 is the approximated height of the shadow of the tabbar. Removing the shadow Makes the text more readable
            top = top[1]-3

            if abs(old_top-top) >= line:
                current_view.set_viewport_position((left, top), preferences.use_animations)

            previous_view = current_view
            i += 1

        preferences.synch_scroll_running = False

class BufferScrollForget(sublime_plugin.ApplicationCommand):

    def run(self, what):

        if what == 'color_scheme':
            sublime.active_window().active_view().settings().erase('color_scheme')

class BufferScrollReFold(sublime_plugin.WindowCommand):

    def run(self):
        view = sublime.active_window().active_view()

        if view is not None:
            id, index = BufferScrollAPI.view_id(view)

            if id in data_base:

                if 'pf' in data_base[id]:
                    rs = []

                    for r in data_base[id]['pf']:
                        rs.append(sublime.Region(int(r[0]), int(r[1])))

                    if len(rs):
                        view.fold(rs)

                    # update the minimap
                    position = view.viewport_position()
                    view.set_viewport_position((position[0]-1,position[1]-1), preferences.use_animations)
                    view.set_viewport_position(position, preferences.use_animations)

    def is_enabled(self):
        view = sublime.active_window().active_view()

        if view is not None and view.file_name():
            id, index = BufferScrollAPI.view_id(view)

            if id in data_base:

                if 'pf' in data_base[id] and len(data_base[id]['pf']):
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

        if not preferences.synch_scroll_running:
            preferences.synch_scroll_running = True
            sublime.set_timeout(lambda:synch_scroll(), 0)

        time.sleep(0.08)

def synch_data_loop():
    synch_data = BufferScrollAPI.synch_data

    while True:

        if not preferences.synch_data_running:
            sublime.set_timeout(lambda:synch_data(None, 'thread'), 0)

        time.sleep(0.5)

def unlockTheScrollRestoring():
    global disable_scroll_restoring

    # log( 1,'disable_scroll_restoring: %s', disable_scroll_restoring )
    disable_scroll_restoring = False

