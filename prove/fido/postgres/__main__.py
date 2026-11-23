"""Adattatore PostgreSQL da solo, stesso trasporto e stesse richieste del collaudo."""

import argparse

from ..trasporto import apri, attendi
from .base import Database


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dsn", required=True, help="socket esplicito del PostgreSQL privato e dbname=prova")
    p.add_argument("--socket", required=True, help="socket dell'adattatore JSONL")
    a = p.parse_args()
    attendi(*apri(a.socket, Database(a.dsn)))


if __name__ == "__main__":
    main()
