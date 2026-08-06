import sys

class Sink:
    def write(self, s):
        pass
    def flush(self):
        pass

print("before:", type(sys.stdout).__name__)
sys.stdout = Sink()
print("after:", type(sys.stdout).__name__)
print("is Sink:", isinstance(sys.stdout, Sink))
