import pkgutil
import langchain

def find_module(package, name):
    for loader, module_name, is_pkg in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        if name in module_name:
            print(module_name)

# try to find where chains is
try:
    import langchain.chains
    print("langchain.chains exists")
except ImportError:
    print("langchain.chains does NOT exist")

# check submodules of langchain
print("Submodules of langchain:")
for loader, module_name, is_pkg in pkgutil.iter_modules(langchain.__path__):
    print(module_name)
