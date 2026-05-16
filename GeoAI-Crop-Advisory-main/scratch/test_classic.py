try:
    import langchain_classic
    print("langchain_classic exists")
    import langchain_classic.chains
    print("langchain_classic.chains exists")
    from langchain_classic.chains import LLMChain
    print("Imported LLMChain from langchain_classic.chains")
except ImportError as e:
    print(f"Error: {e}")
