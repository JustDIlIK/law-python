from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import select, update, inspect, func
from sqlalchemy.orm import ONETOMANY, selectinload, joinedload, aliased

from app.api.services.dates import from_seconds_to_date
from app.db.connection import async_session
from app.db.models import Student, User, Group, Role
from app.db.models.student_history import StudentHistory
from app.db.repository.base import BaseRepository


def filter_model_fields(model, data) -> dict:
    mapper = inspect(model)
    valid_keys = {c.key for c in mapper.attrs}

    if isinstance(data, dict):
        return {k: v for k, v in data.items() if k in valid_keys}

    return {k: getattr(data, k) for k in valid_keys if hasattr(data, k)}


class StudentRepository(BaseRepository):
    model = Student

    @classmethod
    async def add_record(cls, **data):
        async with async_session() as session:
            print(f"{data=}")
            result = await session.execute(
                select(Student).where(
                    Student.student_id_number == data["student_id_number"]
                )
            )
            is_created = False
            stud = result.scalar_one_or_none()

            if stud:
                result = await session.execute(select(User).where(User.id == stud.id))
                user = result.scalar_one_or_none()
                if from_seconds_to_date(data["updated_at"]) > stud.updated_at:
                    print("Here")
                    status_code = data["student_status_code"]
                    history_data = filter_model_fields(StudentHistory, stud)

                    history_data.pop("id")
                    history_data["status_code"] = status_code
                    history = StudentHistory(
                        **history_data, student_id=stud.student_id_number
                    )
                    session.add(history)
                print(f"{stud.level_code=}")

                for key, value in data.items():
                    if hasattr(stud, key):
                        setattr(stud, key, value)
                print(f"{stud.level_code=}")

                for key, value in data.items():
                    if hasattr(user, key):
                        setattr(user, key, value)

                print(f"{stud.full_name=}")
            else:
                is_created = True
                stud = Student(**data)
                session.add(stud)
            await session.commit()
            return is_created

    @classmethod
    async def delete_student(cls, student_id: str):
        async with async_session() as session:
            student_query = select(cls.model).filter_by(student_id_number=student_id)
            student_result = await session.execute(student_query)
            student = student_result.scalar_one_or_none()

            if not student:
                return None

            user_query = update(User).filter_by(id=student.id).values(is_active=False)
            await session.execute(user_query)
            await session.commit()

            return student

    @classmethod
    async def find_all(cls, full_name: str, page: int = 1, limit: int = 50):
        async with async_session() as session:
            offset = (page - 1) * limit
            query = select(cls.model).limit(limit).offset(offset)
            query = query.filter(
                (cls.model.full_name.ilike(f"%{full_name.lower()}%"))
            ).order_by(cls.model.id.desc())

            result = await session.execute(query)
            return result.scalars().all()

    @classmethod
    async def find_all_by_variable(cls, page=1, limit=20, **data):
        async with async_session() as session:

            offset = (page - 1) * limit
            query = select(cls.model).limit(limit).offset(offset).filter_by(**data)

            mapper = inspect(cls.model)
            relationships = mapper.relationships
            fields = relationships.keys()
            load_options = []
            for field in fields:
                if field == "group":
                    continue

                rel_property = relationships[field]
                direction = rel_property.direction
                use_list = rel_property.uselist
                if direction == ONETOMANY or use_list is False:
                    loader = selectinload(getattr(cls.model, field))
                else:
                    loader = joinedload(getattr(cls.model, field))
                load_options.append(loader)

            load_options.append(
                joinedload(cls.model.group).joinedload(Group.education_lang)
            )

            query = query.options(*load_options)

            result = await session.execute(query)
            result = result.unique().scalars().all()

            total_query = select(func.count()).select_from(cls.model).filter_by(**data)
            total = await session.scalar(total_query)

            return {
                "data": result,
                "total": total,
            }

    @classmethod
    async def find_students(
        cls,
        education_year_code: str = "",
        semester_code: str = "",
        page: int = 1,
        limit: int = 50,
        query: str = "",
        gender_code: str = "",
        education_type_code: str = "",
        student_status_code: str = "",
        level_code: str = "",
    ):
        async with async_session() as session:
            offset = (page - 1) * limit
            stmt = select(cls.model).limit(limit).offset(offset)

            filters = []

            if education_year_code:
                filters.append(cls.model.education_year_code == education_year_code)
            if semester_code:
                filters.append(cls.model.semester_code == semester_code)
            if gender_code:
                filters.append(cls.model.gender_code == gender_code)
            if education_type_code:
                filters.append(cls.model.education_type_code == education_type_code)
            if student_status_code:
                filters.append(cls.model.student_status_code == student_status_code)
            if level_code:
                filters.append(cls.model.level_code == level_code)

            if query:
                stmt = stmt.filter(cls.model.full_name.ilike(f"%{query}%"))

            if filters:
                stmt = stmt.filter(*filters)

            mapper = inspect(cls.model)
            load_options = []
            for field, rel_property in mapper.relationships.items():
                if field in [
                    "group",
                    "subjects",
                    "student_achievements",
                    "university",
                    "citizenship",
                    "student_type",
                    "social_category",
                    "accommodation",
                ]:
                    continue
                loader = (
                    selectinload(getattr(cls.model, field))
                    if rel_property.uselist
                    or rel_property.direction.name == "ONETOMANY"
                    else joinedload(getattr(cls.model, field))
                )
                load_options.append(loader)

            load_options.append(
                joinedload(cls.model.group).joinedload(Group.education_lang)
            )

            stmt = stmt.options(*load_options)

            result = await session.execute(stmt)
            students = result.unique().scalars().all()

            total_stmt = select(func.count()).select_from(cls.model)
            if filters:
                total_stmt = total_stmt.filter(*filters)
            if query:
                total_stmt = total_stmt.filter(cls.model.full_name.ilike(f"%{query}%"))
            total = await session.scalar(total_stmt)

            return {
                "data": students,
                "total": total,
            }

    async def get_student_from_user_id(user_id: int):
        async with async_session() as session:
            user_alias = aliased(User)

            query = (
                select(Student)
                .join(user_alias, Student.id == user_alias.id)
                .join(Role, user_alias.role_id == Role.id)
                .where(Role.name == "student", user_alias.id == user_id)
            )

            result = await session.execute(query)
            return result.scalar_one_or_none()

    @classmethod
    async def update_data(cls, id: int, **data):
        async with async_session() as session:
            user_fields = {
                c.key
                for c in inspect(User).mapper.columns
                if c.table.name == User.__tablename__
            }

            student_fields = {
                c.key
                for c in inspect(Student).mapper.columns
                if c.table.name == Student.__tablename__
            }

            user_data = {k: v for k, v in data.items() if k in user_fields}
            student_data = {k: v for k, v in data.items() if k in student_fields}

            if user_data:
                query_user = (
                    update(User.__table__).where(User.id == id).values(**user_data)
                )
                await session.execute(query_user)

            if student_data:
                query_student = (
                    update(Student.__table__)
                    .where(Student.id == id)
                    .values(**student_data)
                )
                await session.execute(query_student)

            await session.commit()
