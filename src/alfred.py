# encoding: utf-8

import logging
import logging.handlers
import os
import sys
import time
import  xml.etree.ElementTree  as ET


UNSET = object()

ICON_ROOT = '/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources'
ICON_ERROR = os.path.join(ICON_ROOT, 'AlertStopIcon.icns')


class Item(object):
    """Represents a feedback item for Alfred.

    Generates Alfred-compliant XML for a single item.

    You probably shouldn't use this class directly, but via
    :meth:`Workflow.add_item`. See :meth:`~Workflow.add_item`
    for details of arguments.

    """

    def __init__(self, title, subtitle='', modifier_subtitles=None,
                 arg=None, autocomplete=None, valid=False, uid=None,
                 icon=None, icontype=None, type=None, largetext=None,
                 copytext=None, quicklookurl=None):
        """Same arguments as :meth:`Workflow.add_item`."""
        self.title = title
        self.subtitle = subtitle
        self.modifier_subtitles = modifier_subtitles or {}
        self.arg = arg
        self.autocomplete = autocomplete
        self.valid = valid
        self.uid = uid
        self.icon = icon
        self.icontype = icontype
        self.type = type
        self.largetext = largetext
        self.copytext = copytext
        self.quicklookurl = quicklookurl

    @property
    def elem(self):
        """Create and return feedback item for Alfred.

        :returns: :class:`ElementTree.Element <xml.etree.ElementTree.Element>`
            instance for this :class:`Item` instance.

        """
        # Attributes on <item> element
        attr = {}
        if self.valid:
            attr['valid'] = 'yes'
        else:
            attr['valid'] = 'no'
        # Allow empty string for autocomplete. This is a useful value,
        # as TABing the result will revert the query back to just the
        # keyword
        if self.autocomplete is not None:
            attr['autocomplete'] = self.autocomplete

        # Optional attributes
        for name in ('uid', 'type'):
            value = getattr(self, name, None)
            if value:
                attr[name] = value

        root = ET.Element('item', attr)
        ET.SubElement(root, 'title').text = self.title
        ET.SubElement(root, 'subtitle').text = self.subtitle

        # Add modifier subtitles
        for mod in ('cmd', 'ctrl', 'alt', 'shift', 'fn'):
            if mod in self.modifier_subtitles:
                ET.SubElement(root, 'subtitle',
                              {'mod': mod}).text = self.modifier_subtitles[mod]

        # Add arg as element instead of attribute on <item>, as it's more
        # flexible (newlines aren't allowed in attributes)
        if self.arg:
            ET.SubElement(root, 'arg').text = self.arg

        # Add icon if there is one
        if self.icon:
            if self.icontype:
                attr = dict(type=self.icontype)
            else:
                attr = {}
            ET.SubElement(root, 'icon', attr).text = self.icon

        if self.largetext:
            ET.SubElement(root, 'text',
                          {'type': 'largetype'}).text = self.largetext

        if self.copytext:
            ET.SubElement(root, 'text',
                          {'type': 'copy'}).text = self.copytext

        if self.quicklookurl:
            ET.SubElement(root, 'quicklookurl').text = self.quicklookurl

        return root


class Workflow(object):
    """The ``Workflow`` object is the main interface to Alfred-Workflow.

    It provides APIs for accessing the Alfred/workflow environment,
    storing & caching data, using Keychain, and generating Script
    Filter feedback.

    ``Workflow`` is compatible with both Alfred 2 and 3. The
    :class:`~workflow.Workflow3` subclass provides additional,
    Alfred 3-only features, such as workflow variables.

    :param default_settings: default workflow settings. If no settings file
        exists, :class:`Workflow.settings` will be pre-populated with
        ``default_settings``.
    :type default_settings: :class:`dict`
    :param update_settings: settings for updating your workflow from
        GitHub releases. The only required key is ``github_slug``,
        whose value must take the form of ``username/repo``.
        If specified, ``Workflow`` will check the repo's releases
        for updates. Your workflow must also have a semantic version
        number. Please see the :ref:`User Manual <user-manual>` and
        `update API docs <api-updates>` for more information.
    :type update_settings: :class:`dict`
    :param input_encoding: encoding of command line arguments. You
        should probably leave this as the default (``utf-8``), which
        is the encoding Alfred uses.
    :type input_encoding: :class:`unicode`
    :param normalization: normalisation to apply to CLI args.
        See :meth:`Workflow.decode` for more details.
    :type normalization: :class:`unicode`
    :param capture_args: Capture and act on ``workflow:*`` arguments. See
        :ref:`Magic arguments <magic-arguments>` for details.
    :type capture_args: :class:`Boolean`
    :param libraries: sequence of paths to directories containing
        libraries. These paths will be prepended to ``sys.path``.
    :type libraries: :class:`tuple` or :class:`list`
    :param help_url: URL to webpage where a user can ask for help with
        the workflow, report bugs, etc. This could be the GitHub repo
        or a page on AlfredForum.com. If your workflow throws an error,
        this URL will be displayed in the log and Alfred's debugger. It can
        also be opened directly in a web browser with the ``workflow:help``
        :ref:`magic argument <magic-arguments>`.
    :type help_url: :class:`unicode` or :class:`str`

    """

    # Which class to use to generate feedback items. You probably
    # won't want to change this
    item_class = Item

    def __init__(self, default_settings=None, 
                 capture_args=True, libraries=None,
                 help_url=None):
        """Create new :class:`Workflow` object."""
        self._default_settings = default_settings or {}
        self._capture_args = capture_args
        self.help_url = help_url
        self._workflowdir = None
        self._settings_path = None
        self._settings = None
        self._bundleid = None
        self._debugging = None
        self._name = None
        self._cache_serializer = 'cpickle'
        self._data_serializer = 'cpickle'
        self._info = None
        self._info_loaded = False
        self._logger = None
        self._items = []
        self._alfred_env = None
        # Version number of the workflow
        self._version = UNSET
        # Version from last workflow run
        self._last_version_run = UNSET
        # Cache for regex patterns created for filter keys
        self._search_pattern_cache = {}
        # Magic arguments
        #: The prefix for all magic arguments. Default is ``workflow:``
        self.magic_prefix = 'workflow:'
        #: Mapping of available magic arguments. The built-in magic
        #: arguments are registered by default. To add your own magic arguments
        #: (or override built-ins), add a key:value pair where the key is
        #: what the user should enter (prefixed with :attr:`magic_prefix`)
        #: and the value is a callable that will be called when the argument
        #: is entered. If you would like to display a message in Alfred, the
        #: function should return a ``unicode`` string.
        #:
        #: By default, the magic arguments documented
        #: :ref:`here <magic-arguments>` are registered.
        self.magic_arguments = {}

        if libraries:
            sys.path = libraries + sys.path

    @property
    def alfred_env(self):
        """Dict of Alfred's environmental variables minus ``alfred_`` prefix.

        .. versionadded:: 1.7

        The variables Alfred 2.4+ exports are:

        ============================  =========================================
        Variable                      Description
        ============================  =========================================
        alfred_debug                  Set to ``1`` if Alfred's debugger is
                                      open, otherwise unset.
        alfred_preferences            Path to Alfred.alfredpreferences
                                      (where your workflows and settings are
                                      stored).
        alfred_preferences_localhash  Machine-specific preferences are stored
                                      in ``Alfred.alfredpreferences/preferences/local/<hash>``
                                      (see ``alfred_preferences`` above for
                                      the path to ``Alfred.alfredpreferences``)
        alfred_theme                  ID of selected theme
        alfred_theme_background       Background colour of selected theme in
                                      format ``rgba(r,g,b,a)``
        alfred_theme_subtext          Show result subtext.
                                      ``0`` = Always,
                                      ``1`` = Alternative actions only,
                                      ``2`` = Selected result only,
                                      ``3`` = Never
        alfred_version                Alfred version number, e.g. ``'2.4'``
        alfred_version_build          Alfred build number, e.g. ``277``
        alfred_workflow_bundleid      Bundle ID, e.g.
                                      ``net.deanishe.alfred-mailto``
        alfred_workflow_cache         Path to workflow's cache directory
        alfred_workflow_data          Path to workflow's data directory
        alfred_workflow_name          Name of current workflow
        alfred_workflow_uid           UID of workflow
        alfred_workflow_version       The version number specified in the
                                      workflow configuration sheet/info.plist
        ============================  =========================================

        **Note:** all values are Unicode strings except ``version_build`` and
        ``theme_subtext``, which are integers.

        :returns: ``dict`` of Alfred's environmental variables without the
            ``alfred_`` prefix, e.g. ``preferences``, ``workflow_data``.

        """
        if self._alfred_env is not None:
            return self._alfred_env

        data = {}

        for key in (
                'alfred_debug',
                'alfred_preferences',
                'alfred_preferences_localhash',
                'alfred_theme',
                'alfred_theme_background',
                'alfred_theme_subtext',
                'alfred_version',
                'alfred_version_build',
                'alfred_workflow_bundleid',
                'alfred_workflow_cache',
                'alfred_workflow_data',
                'alfred_workflow_name',
                'alfred_workflow_uid',
                'alfred_workflow_version'):

            value = os.getenv(key)

            if isinstance(value, str):
                if key in ('alfred_debug', 'alfred_version_build',
                           'alfred_theme_subtext'):
                    value = int(value)
                else:
                    value = value

            data[key[7:]] = value

        self._alfred_env = data

        return self._alfred_env

    @property
    def debugging(self):
        """Whether Alfred's debugger is open.

        :returns: ``True`` if Alfred's debugger is open.
        :rtype: ``bool``

        """
        if self._debugging is None:
            if self.alfred_env.get('debug') == 1:
                self._debugging = True
            else:
                self._debugging = False
        return self._debugging
            
    @property
    def logger(self):
        """Logger that logs to both console and a log file.

        If Alfred's debugger is open, log level will be ``DEBUG``,
        else it will be ``INFO``.

        Use :meth:`open_log` to open the log file in Console.

        :returns: an initialised :class:`~logging.Logger`

        """
        if self._logger:
            return self._logger

        # Initialise new logger and optionally handlers
        logger = logging.getLogger('workflow')

        if not len(logger.handlers):  # Only add one set of handlers

            fmt = logging.Formatter(
                '%(asctime)s %(filename)s:%(lineno)s'
                ' %(levelname)-8s %(message)s',
                datefmt='%H:%M:%S')

            console = logging.StreamHandler()
            console.setFormatter(fmt)
            logger.addHandler(console)

        if self.debugging:
            logger.setLevel(logging.DEBUG)
        else:
            logger.setLevel(logging.INFO)

        self._logger = logger

        return self._logger

    def run(self, func, text_errors=False):
        """Call ``func`` to run your workflow.

        :param func: Callable to call with ``self`` (i.e. the :class:`Workflow`
            instance) as first argument.
        :param text_errors: Emit error messages in plain text, not in
            Alfred's XML/JSON feedback format. Use this when you're not
            running Alfred-Workflow in a Script Filter and would like
            to pass the error message to, say, a notification.
        :type text_errors: ``Boolean``

        ``func`` will be called with :class:`Workflow` instance as first
        argument.

        ``func`` should be the main entry point to your workflow.

        Any exceptions raised will be logged and an error message will be
        output to Alfred.

        """
        start = time.time()

        # Call workflow's entry function/method within a try-except block
        # to catch any errors and display an error message in Alfred
        try:
            # Run workflow's entry function/method
            func(self)

        except Exception as err:
        
                if text_errors:
                    print(err.encode('utf-8'), end='')
                else:
                    self._items = []
                    if self._name:
                        name = self._name
                    elif self._bundleid:
                        name = self._bundleid
                    else:  # pragma: no cover
                        name = os.path.dirname(__file__)
                    self.add_item("Error in workflow '%s'" % name,
                                  err,
                                  icon=ICON_ERROR)
                    self.send_feedback()

        finally:
            self.logger.debug('Workflow finished in {0:0.3f} seconds.'.format(
                time.time() - start))

        return 0

    def add_item(self, title, subtitle='', modifier_subtitles=None, arg=None,
                 autocomplete=None, valid=False, uid=None, icon=None,
                 icontype=None, type=None, largetext=None, copytext=None,
                 quicklookurl=None):
        """Add an item to be output to Alfred.

        :param title: Title shown in Alfred
        :type title: ``unicode``
        :param subtitle: Subtitle shown in Alfred
        :type subtitle: ``unicode``
        :param modifier_subtitles: Subtitles shown when modifier
            (CMD, OPT etc.) is pressed. Use a ``dict`` with the lowercase
            keys ``cmd``, ``ctrl``, ``shift``, ``alt`` and ``fn``
        :type modifier_subtitles: ``dict``
        :param arg: Argument passed by Alfred as ``{query}`` when item is
            actioned
        :type arg: ``unicode``
        :param autocomplete: Text expanded in Alfred when item is TABbed
        :type autocomplete: ``unicode``
        :param valid: Whether or not item can be actioned
        :type valid: ``Boolean``
        :param uid: Used by Alfred to remember/sort items
        :type uid: ``unicode``
        :param icon: Filename of icon to use
        :type icon: ``unicode``
        :param icontype: Type of icon. Must be one of ``None`` , ``'filetype'``
           or ``'fileicon'``. Use ``'filetype'`` when ``icon`` is a filetype
           such as ``'public.folder'``. Use ``'fileicon'`` when you wish to
           use the icon of the file specified as ``icon``, e.g.
           ``icon='/Applications/Safari.app', icontype='fileicon'``.
           Leave as `None` if ``icon`` points to an actual
           icon file.
        :type icontype: ``unicode``
        :param type: Result type. Currently only ``'file'`` is supported
            (by Alfred). This will tell Alfred to enable file actions for
            this item.
        :type type: ``unicode``
        :param largetext: Text to be displayed in Alfred's large text box
            if user presses CMD+L on item.
        :type largetext: ``unicode``
        :param copytext: Text to be copied to pasteboard if user presses
            CMD+C on item.
        :type copytext: ``unicode``
        :param quicklookurl: URL to be displayed using Alfred's Quick Look
            feature (tapping ``SHIFT`` or ``⌘+Y`` on a result).
        :type quicklookurl: ``unicode``
        :returns: :class:`Item` instance

        See :ref:`icons` for a list of the supported system icons.

        .. note::

            Although this method returns an :class:`Item` instance, you don't
            need to hold onto it or worry about it. All generated :class:`Item`
            instances are also collected internally and sent to Alfred when
            :meth:`send_feedback` is called.

            The generated :class:`Item` is only returned in case you want to
            edit it or do something with it other than send it to Alfred.

        """
        item = self.item_class(title, subtitle, modifier_subtitles, arg,
                               autocomplete, valid, uid, icon, icontype, type,
                               largetext, copytext, quicklookurl)
        self._items.append(item)
        return item

    def send_feedback(self):
        """Print stored items to console/Alfred as XML."""
        root = ET.Element('items')
        for item in self._items:
            root.append(item.elem)
        sys.stdout.write('<?xml version="1.0" encoding="utf-8"?>\n')
        sys.stdout.write(ET.tostring(root).decode('utf-8'))
        sys.stdout.flush()
