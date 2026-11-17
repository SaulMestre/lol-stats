"""Render entrypoint for LoL Solo/Duo data collection."""
import runpy

if __name__ == "__main__":
    runpy.run_module("research_server", run_name="__main__")
