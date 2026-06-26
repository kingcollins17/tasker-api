from sqlalchemy.types import TypeDecorator, String
from geoalchemy2 import Geometry

class PointType(TypeDecorator):
    impl = Geometry
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect is None or dialect.name == "sqlite":
            return String()
        else:
            return Geometry(geometry_type="POINT", srid=4326, spatial_index=True)


class GeometryType(TypeDecorator):
    impl = Geometry
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect is None or dialect.name == "sqlite":
            return String()
        else:
            return Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=True)
