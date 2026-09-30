"""Install ``tiktoken`` on first use, into a directory this plugin owns.

``codex plugin add`` copies files and has no step that installs Python packages, so a fresh
install had no ``tiktoken`` and the content panel stayed empty until someone ran pip by hand.
The report now installs it itself, once, the first time a run needs it:

    <CODEX_HOME>/token-counter/lib/<interpreter>-<platform>/

with ``pip install --target``. The user's own environment is never touched: no
site-packages write, no ``--user``, no PEP 668 override. The directory is keyed by
interpreter and platform because ``tiktoken`` ships a native extension that loads only in
the interpreter it was built for. Deleting the directory undoes it.

This is the report's one network call, to PyPI, made only when ``tiktoken`` is not already
importable. ``--no-install`` or ``TOKEN_COUNTER_NO_INSTALL=1`` turns it off, and the run then
degrades as it always has (ARCHITECTURE.md section 4.2).
"""
import importlib
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile

REQUIREMENT = 'tiktoken'
ENV_OFF = 'TOKEN_COUNTER_NO_INSTALL'
TIMEOUT = 300                # seconds for the whole install; a hung proxy must not hang the run


def tag():
    """``cpython-311-linux-x86_64``: what a native wheel is built for."""
    impl = (sys.implementation.cache_tag
            or f'py{sys.version_info[0]}{sys.version_info[1]}')
    return f'{impl}{getattr(sys, "abiflags", "")}-{sysconfig.get_platform()}'


def roots():
    """Where an install may live: ``report.out_dir()`` and its temp-directory fallback."""
    home = os.environ.get('CODEX_HOME') or os.path.join(os.path.expanduser('~'), '.codex')
    return [os.path.join(home, 'token-counter'),
            os.path.join(tempfile.gettempdir(), 'token-counter')]


def lib_dir(root):
    return os.path.join(root, 'lib', tag())


def disabled():
    """``TOKEN_COUNTER_NO_INSTALL`` set to anything but empty or ``0``."""
    return os.environ.get(ENV_OFF, '').strip() not in ('', '0')


def _forget():
    """Drop a half-imported ``tiktoken`` so the next import starts over from ``sys.path``."""
    for k in [k for k in sys.modules if k.split('.')[0] in ('tiktoken', 'tiktoken_ext')]:
        del sys.modules[k]
    importlib.invalidate_caches()


def importable():
    try:
        import tiktoken  # noqa: F401
        return True
    except ImportError:
        _forget()
        return False


def activate(root=None):
    """Put an earlier private install on ``sys.path``.  Returns its directory, or ``None``.

    Prepended, not appended: pip resolved ``tiktoken`` and its dependencies there as one
    set, and an older ``regex`` elsewhere on the path must not be mixed into it.  Worker
    processes inherit ``sys.path`` under fork, spawn and forkserver alike.
    """
    for r in ([root] if root else roots()):
        d = lib_dir(r)
        if os.path.isdir(os.path.join(d, 'tiktoken')):
            if d not in sys.path:
                sys.path.insert(0, d)
            _forget()
            return d
    return None


def installed_version():
    """``(version, file)`` of the ``tiktoken`` this process would use, or ``None``."""
    try:
        import tiktoken
    except ImportError:
        _forget()
        return None
    return getattr(tiktoken, '__version__', '?'), getattr(tiktoken, '__file__', '?')


def ensure(root):
    """Make ``tiktoken`` importable, installing it under `root` if nothing provides it.

    Returns ``None`` once it imports, otherwise one line saying why it could not.  Never
    raises: a failed install costs the content panel, not the run.
    """
    if importable():
        return None
    if activate() and importable():             # an earlier run's install
        return None
    if disabled():
        return f'tiktoken is not installed, and {ENV_OFF} is set'
    target = lib_dir(root)
    print(f'tiktoken is not installed; installing it for token-counter (once, from PyPI) '
          f'into\n  {target}', file=sys.stderr)
    why = _install(target)
    if why:
        return f'tiktoken could not be installed: {why}'
    activate(root)
    got = installed_version()
    if got is None:
        return f'tiktoken was installed into {target} but does not import'
    print(f'installed tiktoken {got[0]}', file=sys.stderr)
    return None


def _install(target):
    """``pip install --target`` into a staging directory, then move it into place.

    Staging keeps a half-finished install from ever being activated: an interrupted run
    leaves a ``.install-*`` directory behind, never a ``tiktoken`` without its extension.
    """
    parent = os.path.dirname(target)
    existed = os.path.isdir(target)
    try:
        os.makedirs(parent, exist_ok=True)
        stage = tempfile.mkdtemp(prefix='.install-', dir=parent)
    except OSError as exc:
        return f'{parent} is not writable ({exc.__class__.__name__})'
    old = stage + '.old'
    try:
        why = _run_installer(stage)
        if why:
            return why
        if os.path.isdir(target):
            if not existed:
                return None         # a concurrent run finished first; its copy is as good
            # The copy there does not import.  Moved aside rather than deleted in place, so
            # a file held open (Windows) cannot leave half of it where the next run looks.
            os.rename(target, old)
        os.rename(stage, target)
        return None
    except OSError as exc:
        return f'could not move the install into place ({exc.__class__.__name__})'
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(old, ignore_errors=True)


def _run_installer(stage):
    """Run pip, or uv where the interpreter has no pip.  Returns ``None`` or a reason."""
    if not sys.executable:
        return 'the interpreter path is unknown'
    # Wheels only: a platform with no wheel would otherwise fall through to building the
    # Rust extension from source, which needs a toolchain and minutes, and then fails.
    args = ['--target', stage, '--only-binary', ':all:', REQUIREMENT]
    env = dict(os.environ)
    # Settings that contradict `--target`, from the user's environment or pip config.
    env.update(PIP_USER='0', PIP_REQUIRE_VIRTUALENV='0', PIP_DISABLE_PIP_VERSION_CHECK='1',
               PIP_NO_INPUT='1')
    # Two retries, not pip's five: where the network is filtered rather than refused, each
    # attempt waits out the connect timeout, and the next run simply tries again.
    pip = [sys.executable, '-m', 'pip', 'install', '--quiet', '--retries', '2'] + args
    p, why = _call(pip, env)
    if why is None and p.returncode == 0:
        return None
    reason = why or _pip_reason(p.stderr + p.stdout)
    if why is None and 'No module named pip' in (p.stderr or ''):
        uv = shutil.which('uv')
        if uv is None:
            return f'pip is not available for {sys.executable}'
        p, why = _call([uv, 'pip', 'install', '--quiet', '--python', sys.executable] + args,
                       env)
        if why is None and p.returncode == 0:
            return None
        reason = why or _pip_reason(p.stderr + p.stdout)
    return reason


def _call(cmd, env):
    try:
        p = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, f'timed out after {TIMEOUT}s'
    except OSError as exc:
        return None, f'{exc.__class__.__name__}: {exc}'
    return p, None


# What an unreachable index looks like from pip (urllib3) and from uv.
_OFFLINE = ('NewConnectionError', 'Failed to establish', 'Temporary failure in name resolution',
            'Name or service not known', 'nodename nor servname', 'getaddrinfo failed',
            'Network is unreachable', 'ProxyError', 'ConnectTimeoutError',
            'error sending request', 'dns error')


def _pip_reason(text):
    """One line from pip's output that says what went wrong."""
    text = text or ''
    if any(s in text for s in _OFFLINE):
        return 'PyPI is unreachable (no network access?)'
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    errors = [ln for ln in lines if ln.lower().startswith(('error', 'x ', '×'))]
    last = (errors or lines or ['the installer failed with no output'])[-1]
    return last[:200]
