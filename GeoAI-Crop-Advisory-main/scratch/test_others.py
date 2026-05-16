try:
    from langchain_text_splitters import CharacterTextSplitter
    print("CharacterTextSplitter success")
except ImportError as e:
    print(f"CharacterTextSplitter fail: {e}")

try:
    from langchain_core.prompts import PromptTemplate
    print("PromptTemplate success")
except ImportError as e:
    print(f"PromptTemplate fail: {e}")

try:
    from langchain_community.vectorstores import FAISS
    print("FAISS success")
except ImportError as e:
    print(f"FAISS fail: {e}")
