import os
import random


def build_groups(photos_root, url_builder, featured_map=None, group_order=None):
    groups = {}
    if os.path.isdir(photos_root):
        for dirpath, _, filenames in os.walk(photos_root):
            rel_dir = os.path.relpath(dirpath, photos_root)
            species = rel_dir if rel_dir != "." else "未分类"
            for filename in sorted(filenames):
                if not filename.lower().endswith((".jpg", ".jpeg")):
                    continue
                rel_path = os.path.join(rel_dir, filename) if rel_dir != "." else filename
                groups.setdefault(species, []).append(
                    {
                        "url": url_builder(rel_path.replace(os.sep, "/")),
                        "name": os.path.splitext(filename)[0],
                        "filename": filename,
                    }
                )

    group_list = []
    featured_map = featured_map or {}
    group_order = group_order or {}

    def sort_key(item):
        species = item[0]
        order = group_order.get(species)
        return (order if order is not None else 10**9, species)

    for species, items in sorted(groups.items(), key=sort_key):
        items.sort(key=lambda item: item["name"])
        by_filename = {item["filename"]: item for item in items}
        featured_names = [
            name for name in featured_map.get(species, []) if name in by_filename
        ][:3]
        stack_photos = [by_filename[name] for name in featured_names]
        remaining = [item for item in items if item["filename"] not in featured_names]
        random.shuffle(remaining)
        for photo in remaining:
            if len(stack_photos) >= 3:
                break
            stack_photos.append(photo)
        group_list.append(
            {
                "species": species,
                "photos": items,
                "count": len(items),
                "stack_photos": stack_photos,
                "featured": featured_names,
            }
        )
    return group_list
