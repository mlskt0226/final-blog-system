from fastapi import FastAPI, Request, HTTPException, Query, Form, Response, Depends, Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from typing import List, Optional

from sqlalchemy.orm import Session

from database import SessionLocal, engine, Base
from models import User, Post, Comment, Favorite

app = FastAPI(title="Blog Platform API", version="1.0")

# создаём таблицы (для простоты, без Alembic на этом шаге)
Base.metadata.create_all(bind=engine)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

# ---------- ЗАВИСИМОСТЬ ДЛЯ СЕССИИ БД ----------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- МОДЕЛИ С ВАЛИДАЦИЕЙ ----------
class UserCreate(BaseModel):
    username: str = Field(..., min_length=2, max_length=50)
    email: str = Field(..., min_length=5, max_length=100)
    password: str = Field(..., min_length=3, max_length=100)


class UserLogin(BaseModel):
    email: str
    password: str


class PostCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    content: str = Field(..., min_length=10, max_length=5000)


class PostOut(BaseModel):
    id: int
    title: str
    content: str
    user_id: int
    rating: int

    class Config:
        orm_mode = True


# ---------- СЕССИИ ----------
def get_current_user_id(request: Request) -> Optional[int]:
    user_id_str = request.cookies.get("user_id")
    if user_id_str:
        try:
            return int(user_id_str)
        except ValueError:
            return None
    return None  # гость


# ---------- ОБРАБОТКА ОШИБOK ВАЛИДАЦИИ ----------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "title": "Ошибка валидации",
            "message": "Проверьте правильность заполнения полей (длина текста, формат email и т.д.)"
        },
        status_code=400
    )


# ---------- HTML ГЛАВНАЯ С ПОИСКОМ И ПАГИНАЦИЕЙ ----------
@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    q: Optional[str] = None,
    page: int = 1,
    db: Session = Depends(get_db)
):
    per_page = 5
    query = db.query(Post)

    if q:
        pattern = f"%{q.lower()}%"
        # ilike для SQLite/Postgres, соответствует рекомендациям по поиску по подстроке.[web:139]
        query = query.filter(
            Post.title.ilike(pattern) | Post.content.ilike(pattern)
        )

    total = query.count()
    paginated_posts = query.offset((page - 1) * per_page).limit(per_page).all()
    total_pages = (total + per_page - 1) // per_page

    comments = db.query(Comment).all()
    comments_by_post = {}
    for c in comments:
        comments_by_post.setdefault(c.post_id, []).append(
            {"author": c.author_name, "text": c.text}
        )

    # избранное сейчас не тянем из БД для всех, только структура для шаблона
    favorites_db = {}
    current_user_id = get_current_user_id(request)
    if current_user_id:
        fav_ids = [
            f.post_id for f in db.query(Favorite).filter(Favorite.user_id == current_user_id).all()
        ]
        favorites_db[current_user_id] = fav_ids

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "posts": paginated_posts,
            "comments_by_post": comments_by_post,
            "title": "Главная — Блог",
            "favorites_db": favorites_db,
            "page": page,
            "total_pages": total_pages,
            "q": q,
        }
    )


# ---------- HTML СТРАНИЦЫ РЕГИСТРАЦИИ/ЛОГИНА ----------
@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "title": "Регистрация"})


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "title": "Вход"})


# ---------- LOGOUT ----------
@app.get("/logout")
def logout(response: Response):
    response.delete_cookie("user_id")
    return RedirectResponse(url="/", status_code=303)


# ---------- РЕГИСТРАЦИЯ С ВАЛИДАЦИЕЙ + БД ----------
@app.post("/auth/register")
def register(
    username: str = Form(..., min_length=2, max_length=50),
    email: str = Form(..., min_length=5, max_length=100),
    password: str = Form(..., min_length=3, max_length=100),
    db: Session = Depends(get_db),
):
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email уже используется")

    user = User(username=username, email=email, password=password, role="USER")
    db.add(user)
    db.commit()
    db.refresh(user)
    return RedirectResponse(url="/login", status_code=303)


@app.post("/auth/login")
def login(
    email: str = Form(..., min_length=5),
    password: str = Form(..., min_length=3),
    db: Session = Depends(get_db),
):
    user_db = db.query(User).filter(User.email == email).first()
    if not user_db or user_db.password != password:
        raise HTTPException(status_code=401, detail="Неверный логин/пароль")

    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(key="user_id", value=str(user_db.id), httponly=True)
    return response


# ---------- ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ ----------
@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, db: Session = Depends(get_db)):
    current_user_id = get_current_user_id(request)
    user = db.query(User).filter(User.id == current_user_id).first() if current_user_id else None

    class Obj:
        pass

    user_obj = None
    if user:
        user_obj = Obj()
        user_obj.id = user.id
        user_obj.username = user.username
        user_obj.email = user.email
        user_obj.role = user.role

    return templates.TemplateResponse(
        "profile.html",
        {"request": request, "title": "Профиль", "user": user_obj}
    )


@app.post("/profile")
def update_profile(
    request: Request,
    username: str = Form(..., min_length=2, max_length=50),
    email: str = Form(..., min_length=5, max_length=100),
    db: Session = Depends(get_db),
):
    current_user_id = get_current_user_id(request)
    user = db.query(User).filter(User.id == current_user_id).first() if current_user_id else None
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    existing = db.query(User).filter(User.email == email, User.id != current_user_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email уже используется другим пользователем")

    user.username = username
    user.email = email
    db.commit()
    db.refresh(user)

    class Obj:
        pass

    user_obj = Obj()
    user_obj.id = user.id
    user_obj.username = user.username
    user_obj.email = user.email
    user_obj.role = user.role

    return templates.TemplateResponse(
        "profile.html",
        {"request": request, "title": "Профиль", "user": user_obj}
    )


# ---------- CRUD ПОСТОВ С ВАЛИДАЦИЕЙ + БД ----------
@app.post("/posts/")
def create_post(
    request: Request,
    title: str = Form(..., min_length=3, max_length=200),
    content: str = Form(..., min_length=10, max_length=5000),
    db: Session = Depends(get_db),
):
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(status_code=403, detail="Нужно войти, чтобы создавать посты")

    db_post = Post(title=title, content=content, user_id=user_id, rating=0)
    db.add(db_post)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.get("/posts/", response_model=List[PostOut])
def get_posts(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    query = db.query(Post)
    if user_id is not None:
        query = query.filter(Post.user_id == user_id)
    posts = query.offset((page - 1) * limit).limit(limit).all()
    return posts


@app.get("/posts/{post_id}", response_model=PostOut)
def get_post(post_id: int, db: Session = Depends(get_db)):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Пост не найден")
    return post


@app.post("/posts/{post_id}/edit")
def edit_post(
    request: Request,
    post_id: int,
    title: str = Form(..., min_length=3, max_length=200),
    content: str = Form(..., min_length=10, max_length=5000),
    db: Session = Depends(get_db),
):
    current_user_id = get_current_user_id(request)
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Пост не найден")
    # позже можно добавить проверку "только автор или админ"
    post.title = title
    post.content = content
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/posts/{post_id}/delete")
def delete_post(
    request: Request,
    post_id: int,
    db: Session = Depends(get_db),
):
    current_user_id = get_current_user_id(request)
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Пост не найден")
    # позже: проверка прав
    db.query(Comment).filter(Comment.post_id == post_id).delete()
    db.query(Favorite).filter(Favorite.post_id == post_id).delete()
    db.delete(post)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


# ---------- ПРОСТАЯ ОЦЕНКА (ЛАЙК/ЗВЕЗДА) В БД ----------
@app.post("/posts/{post_id}/rate")
def rate_post(
    request: Request,
    post_id: int = Path(...),
    db: Session = Depends(get_db),
):
    current_user_id = get_current_user_id(request)
    if not current_user_id:
        raise HTTPException(status_code=403, detail="Только авторизованные пользователи могут оценивать посты")

    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Пост не найден")

    post.rating = (post.rating or 0) + 1
    db.commit()
    return RedirectResponse(url="/", status_code=303)


# ---------- КОММЕНТАРИИ С ВАЛИДАЦИЕЙ + БД ----------
@app.post("/posts/{post_id}/comments")
def create_comment(
    request: Request,
    post_id: int,
    author: str = Form(..., min_length=2, max_length=50),
    text: str = Form(..., min_length=3, max_length=1000),
    db: Session = Depends(get_db),
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Пост не найден")

    current_user_id = get_current_user_id(request)
    comment = Comment(
        post_id=post_id,
        user_id=current_user_id,
        author_name=author,
        text=text,
    )
    db.add(comment)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.get("/posts/{post_id}/comments")
def get_comments(post_id: int, db: Session = Depends(get_db)):
    comments = db.query(Comment).filter(Comment.post_id == post_id).all()
    return [
        {"id": c.id, "post_id": c.post_id, "author": c.author_name, "text": c.text}
        for c in comments
    ]


# ---------- ИЗБРАННЫЕ ПОСТЫ В БД ----------
@app.post("/posts/{post_id}/favorite")
def add_favorite(
    request: Request,
    post_id: int,
    db: Session = Depends(get_db),
):
    current_user_id = get_current_user_id(request)
    if not current_user_id:
        raise HTTPException(status_code=403, detail="Нужно войти, чтобы добавлять в избранное")

    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Пост не найден")

    existing = db.query(Favorite).filter(
        Favorite.user_id == current_user_id,
        Favorite.post_id == post_id
    ).first()
    if not existing:
        fav = Favorite(user_id=current_user_id, post_id=post_id)
        db.add(fav)
        db.commit()

    return RedirectResponse(url="/favorites", status_code=303)


@app.post("/posts/{post_id}/unfavorite")
def remove_favorite(
    request: Request,
    post_id: int,
    db: Session = Depends(get_db),
):
    current_user_id = get_current_user_id(request)
    if not current_user_id:
        raise HTTPException(status_code=403, detail="Нужно войти, чтобы изменять избранное")

    db.query(Favorite).filter(
        Favorite.user_id == current_user_id,
        Favorite.post_id == post_id
    ).delete()
    db.commit()
    return RedirectResponse(url="/favorites", status_code=303)


@app.get("/favorites", response_class=HTMLResponse)
def favorites_page(request: Request, db: Session = Depends(get_db)):
    current_user_id = get_current_user_id(request)
    if not current_user_id:
        fav_posts = []
    else:
        fav_ids = [
            f.post_id for f in db.query(Favorite).filter(Favorite.user_id == current_user_id).all()
        ]
        fav_posts = db.query(Post).filter(Post.id.in_(fav_ids)).all()

    comments = db.query(Comment).all()
    comments_by_post = {}
    for c in comments:
        comments_by_post.setdefault(c.post_id, []).append(
            {"author": c.author_name, "text": c.text}
        )

    favorites_db = {}
    if current_user_id:
        favorites_db[current_user_id] = [p.id for p in fav_posts]

    return templates.TemplateResponse(
        "favorites.html",
        {
            "request": request,
            "title": "Избранные посты",
            "posts": fav_posts,
            "comments_by_post": comments_by_post,
            "favorites_db": favorites_db,
        }
    )


# ---------- ПОИСК ----------
@app.get("/search/posts/")
def search_posts(
    q: str,
    page: int = 1,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    pattern = f"%{q.lower()}%"
    query = db.query(Post).filter(
        Post.title.ilike(pattern) | Post.content.ilike(pattern)
    )
    posts = query.offset((page - 1) * limit).limit(limit).all()
    return posts


@app.get("/search/users/")
def search_users(
    q: str,
    db: Session = Depends(get_db),
):
    pattern = f"%{q.lower()}%"
    users = db.query(User).filter(
        User.username.ilike(pattern) | User.email.ilike(pattern)
    ).all()
    return [
        {"id": u.id, "username": u.username, "email": u.email, "role": u.role}
        for u in users
    ]
