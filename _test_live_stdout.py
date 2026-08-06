import sys
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

l = Live(Panel(Text('test')), screen=False)
l.start()
print("type after live:", type(sys.stdout).__name__)
print("module after live:", sys.stdout.__class__.__module__)
class Sink:
    def write(self, s):
        pass
    def flush(self):
        pass
sys.stdout = Sink()
print("type after assign:", type(sys.stdout).__name__)
l.stop()
