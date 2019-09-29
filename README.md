# Description

Buffer Scroll is a simple [Sublime Text](http://www.sublimetext.com/ ) plug-in which remembers and restores the scroll, cursor positions, also the selections, marks, bookmarks, foldings, selected syntax and optionally the colour scheme, when you open a file. Will also remember different data depending the position of the file in the application (example file1 in window1 has scroll line 30, file1 in window2 has scroll in line 40)

Also, via preferences, allows to enable syncing of scroll, bookmarks, marks and folds between cloned views, live.

Syncing features are disabled by default. You need to enable these via the preferences. Main menu -> Preferences -> Package Settings -> BufferScroll -> Settings Default.
You may want to copy and paste your edited preferences to "Settings Users" located under the same sub-menu. To keep your preferences between updates.

Requested by Kensai this package now provides "typewriter scrolling":  The line you work with is automatically the vertical center of the screen.

There is also a hidden feature "refold", if you unfolded code, you can select "refold" from the command palette. This will only work of course if you don't touch the code between the unfold and refold.

Requested by  Binocular222 there is now option to select Folded/Unfolded regions.

<img src="http://dl.dropbox.com/u/9303546/SublimeText/BufferScoll/sync-scroll.png" border="0"/>


## Installation

### By Package Control

1. Download & Install **`Sublime Text 3`** (https://www.sublimetext.com/3)
1. Go to the menu **`Tools -> Install Package Control`**, then,
   wait few seconds until the installation finishes up
1. Go to the menu **`Tools -> Command Palette...
   (Ctrl+Shift+P)`**
1. Type **`Preferences:
   Package Control Settings – User`** on the opened quick panel and press <kbd>Enter</kbd>
1. Then,
   add the following setting to your **`Package Control.sublime-settings`** file, if it is not already there
   ```js
   [
       ...
       "channels":
       [
           "https://raw.githubusercontent.com/evandrocoan/StudioChannel/master/channel.json",
           "https://packagecontrol.io/channel_v3.json",
       ],
       ...
   ]
   ```
   * Note,
     the **`https://raw...`** line must to be added before the **`https://packagecontrol...`**,
     otherwise you will not install this forked version of the package,
     but the original available on the Package Control default channel **`https://packagecontrol...`**
1. Now,
   go to the menu **`Preferences -> Package Control`**
1. Type **`Install Package`** on the opened quick panel and press <kbd>Enter</kbd>
1. Then,
search for **`BufferScroll`** and press <kbd>Enter</kbd>

See also:
1. [ITE - Integrated Toolset Environment](https://github.com/evandrocoan/ITE)
1. [Package control docs](https://packagecontrol.io/docs/usage) for details.


# Bugs

 * The application does not have an event listener for when you switch, open or close projects, and windows, then this package can't save the data for the focused files. By not providing these listeners, this package has suboptimal methods for saving it's data. Such listening change of views, focus lost, file closing, saving, etc.
 * For some reason sublime API is not restoring scroll of xml/html documents, including: xml, tpl, html, xhtml See: http://www.sublimetext.com/forum/viewtopic.php?f=3&t=6237&start=0 Also this will and is causing problems for these that use the build screen with F4. Also, is causing another problem, that you can't request to sublime to open a file at a given row, because this plugin will overwrite the scrolled line. http://www.sublimetext.com/forum/viewtopic.php?f=5&t=3503&start=20#p22324
 * There is no event listener for when a view is scrolled, then this package, has another suboptimal way of listening for changes of scroll, such tracking the scroll every x time.


# Forum Thread

http://www.sublimetext.com/forum/viewtopic.php?f=5&t=3503

# License

See license.txt
