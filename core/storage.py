"""Permanent file storage on Cloudinary (used when CLOUDINARY_URL is set).

Render's free disk is wiped on every restart, so uploads must live elsewhere.
- Medicine photos (names starting with PUBLIC_PREFIX) are public images, resized on the fly.
- Everything else (pharmacy licences, prescriptions) is private ("authenticated"): it can only be
  fetched with a signed link, and the site only opens it inside its own access-checked views.
"""
import posixpath

import requests
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible

PUBLIC_PREFIX = 'medicine-photos/'


def _is_public(name):
    return name.startswith(PUBLIC_PREFIX)


def _image_id(name):
    """Cloudinary keeps an image's extension outside its public id."""
    return posixpath.splitext(name)[0]


@deconstructible
class CloudinaryStorage(Storage):
    def _target(self, name):
        if _is_public(name):
            return {'public_id': _image_id(name), 'resource_type': 'image', 'type': 'upload'}
        # "raw" keeps the extension in the public id and accepts PDFs and images alike.
        return {'public_id': name, 'resource_type': 'raw', 'type': 'authenticated'}

    def _save(self, name, content):
        import cloudinary.uploader
        content.seek(0)
        cloudinary.uploader.upload(content, overwrite=False, unique_filename=False,
                                   use_filename=False, invalidate=True, **self._target(name))
        return name

    def _open(self, name, mode='rb'):
        import cloudinary.utils
        target = self._target(name)
        url, _ = cloudinary.utils.cloudinary_url(target['public_id'], resource_type=target['resource_type'],
                                                 type=target['type'], sign_url=True, secure=True)
        response = requests.get(url, timeout=30)
        if response.status_code == 404:
            raise FileNotFoundError(name)
        response.raise_for_status()
        file = ContentFile(response.content)
        file.name = name
        return file

    def delete(self, name):
        if not name:
            return
        import cloudinary.uploader
        target = self._target(name)
        cloudinary.uploader.destroy(target['public_id'], resource_type=target['resource_type'],
                                    type=target['type'], invalidate=True)

    def exists(self, name):
        return False  # upload paths contain random parts; never block a save

    def get_available_name(self, name, max_length=None):
        return name

    def url(self, name, width=900):
        if not _is_public(name):
            raise ValueError('Private files are only served through the site\'s own access-checked views.')
        import cloudinary.utils
        url, _ = cloudinary.utils.cloudinary_url(
            _image_id(name), resource_type='image', type='upload', secure=True,
            transformation=[{'width': width, 'height': width, 'crop': 'limit'},
                            {'fetch_format': 'auto', 'quality': 'auto'}])
        return url

    def thumb_url(self, name, size=160):
        if not _is_public(name):
            return ''
        import cloudinary.utils
        url, _ = cloudinary.utils.cloudinary_url(
            _image_id(name), resource_type='image', type='upload', secure=True,
            transformation=[{'width': size, 'height': size, 'crop': 'fill', 'gravity': 'auto'},
                            {'fetch_format': 'auto', 'quality': 'auto'}])
        return url
