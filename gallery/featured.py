import os
import sqlite3


def _ensure_db(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        create table if not exists featured (
            species text not null,
            filename text not null,
            rank integer not null,
            primary key (species, rank)
        )
        """
    )
    conn.execute(
        "create unique index if not exists featured_unique on featured(species, filename)"
    )
    conn.execute(
        """
        create table if not exists group_order (
            species text primary key,
            position integer not null
        )
        """
    )
    conn.commit()
    return conn


def get_featured_map(db_path):
    conn = _ensure_db(db_path)
    rows = conn.execute(
        "select species, filename, rank from featured order by rank"
    ).fetchall()
    conn.close()
    featured = {}
    for species, filename, rank in rows:
        featured.setdefault(species, []).append((rank, filename))
    return {
        species: [filename for _, filename in sorted(items)]
        for species, items in featured.items()
    }


def set_featured(db_path, species, filenames):
    conn = _ensure_db(db_path)
    conn.execute("delete from featured where species = ?", (species,))
    for index, name in enumerate(filenames, start=1):
        conn.execute(
            "insert into featured (species, filename, rank) values (?, ?, ?)",
            (species, name, index),
        )
    conn.commit()
    conn.close()


def get_group_order(db_path):
    conn = _ensure_db(db_path)
    rows = conn.execute(
        "select species, position from group_order order by position"
    ).fetchall()
    conn.close()
    return {species: position for species, position in rows}


def set_group_order(db_path, species_list):
    conn = _ensure_db(db_path)
    conn.execute("delete from group_order")
    for index, species in enumerate(species_list, start=1):
        conn.execute(
            "insert into group_order (species, position) values (?, ?)",
            (species, index),
        )
    conn.commit()
    conn.close()
