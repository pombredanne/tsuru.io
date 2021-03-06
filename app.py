# Copyright 2013 Globo.com. All rights reserved.
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file.

import copy
import hashlib
import os
import sys

import pymongo
import requests
import werkzeug

from flask import Flask, render_template, flash, g, request, url_for, redirect
from flask_s3 import FlaskS3
from flaskext.babel import Babel, lazy_gettext as _

import forms
from countries import country_choices


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "secret")
app.config["DEBUG"] = int(os.environ.get("BETA_DEBUG", 1)) != 0

bucket = os.environ.get("TSURU_S3_BUCKET")
app.config["S3_BUCKET_NAME"] = bucket
FlaskS3(app)

babel = Babel(app)

MONGO_URI = os.environ.get("MONGO_URI", "localhost:27017")
MONGO_USER = os.environ.get("MONGO_USER", "")
MONGO_PASSWORD = os.environ.get("MONGO_PASSWORD", "")
MONGO_DATABASE_NAME = os.environ.get("MONGO_DATABASE_NAME", "beta_test")
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
FACEBOOK_APP_ID = os.environ.get("FACEBOOK_APP_ID", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_USER_IP = os.environ.get("GOOGLE_USER_IP")
GOOGLE_OAUTH_ENDPOINT = "https://www.googleapis.com/oauth2/v2"
SIGN_KEY = os.environ.get("SIGN_KEY")


@app.context_processor
def language():
    return dict(language=get_locale())


@babel.localeselector
def get_locale():
    if request.cookies.get('language'):
        l = (request.cookies.get('language'), 1)
        accept = werkzeug.datastructures.LanguageAccept([l])
    else:
        accept = request.accept_languages
    return accept.best_match(['pt', 'en'])


def sign(email):
    h = hashlib.sha1(email)
    h.update(SIGN_KEY)
    return h.hexdigest()


def save_user(first_name, last_name, email, identity=None, redirect_to=None):
    user = {"first_name": first_name, "last_name": last_name, "email": email}
    if identity:
        user["identity"] = identity
    if g.db.users.find({"email": email}).count() > 0:
        if redirect_to:
            return redirect(redirect_to)
        return render_template("confirmation.html", registered=True), 200
    g.db.users.insert(user)
    return render_template("confirmation.html",
                           form=get_survey_form(email)), 200


def get_survey_form(email, f=None):
    form = forms.SurveyForm(f)
    form.country.choices = country_choices[:4]
    other = copy.copy(country_choices[4:])
    other.sort(key=lambda x: x[1])
    form.country.choices.extend(other)
    if f is None:
        form.email.data = email
        form.signature.data = sign(email)
    return form


def _try(form):
    return render_template("try.html", facebook_app_id=FACEBOOK_APP_ID,
                           github_client_id=GITHUB_CLIENT_ID, form=form), 200


@app.route("/")
def index():
    return render_template("index.html", form=forms.SignupForm()), 200


@app.route("/try")
def try_tsuru():
    return _try(forms.SignupForm())


@app.route("/signup", methods=["POST"])
def signup():
    f = forms.SignupForm(request.form)
    if f.validate():
        return save_user(f.first_name.data, f.last_name.data,
                         f.email.data, identity=f.identity.data)
    return _try(f)


@app.route("/about")
def about():
    return render_template("about.html"), 200


@app.route("/community")
def community():
    return render_template("community.html"), 200


@app.route("/survey", methods=["POST"])
def survey():
    if not get_survey_form(None, request.form).validate():
        return "Invalid email.", 400
    if sign(request.form["email"]) != request.form["signature"]:
        msg = "Signatures don't match. You're probably doing something nasty."
        return msg, 400
    survey = {
        "email": request.form["email"],
        "work": request.form["work"],
        "country": request.form["country"],
        "organization": request.form["organization"],
        "why": request.form["why"],
    }
    g.db.survey.insert(survey)
    return render_template("confirmation.html", registered=True), 201


@app.route("/register/facebook")
def facebook_register():
    try:
        if not has_token(request.args):
            return "Could not obtain access token from facebook.", 400
        url = "https://graph.facebook.com/me?"
        url += "fields=first_name,last_name,email&access_token={0}"
        url = url.format(request.args["access_token"])
        response = requests.get(url)
        info = response.json()
        return save_user(info["first_name"], info["last_name"], info["email"])
    except Exception as e:
        sys.stderr.write("%s\n" % e)
        flash(_("We weren't able to get your email from Facebook, "
                "please use one of the other options for signing up."))
        return redirect(url_for("try_tsuru"))


@app.route("/register/github")
def github_register():
    try:
        code = request.args.get("code")
        if code is None:
            return "Could not obtain code access to github.", 400
        data = "client_id={0}&code={1}&client_secret={2}".format(
            GITHUB_CLIENT_ID,
            code,
            GITHUB_CLIENT_SECRET
        )
        headers = {"Accept": "application/json"}
        url = "https://github.com/login/oauth/access_token"
        response = requests.post(url, data=data, headers=headers)
        token = response.json().get("access_token")
        if token is None or token == "":
            return "Could not obtain access token from github.", 400
        url = "https://api.github.com/user?access_token={0}".format(token)
        response = requests.get(url, headers=headers)
        info = response.json()
        first_name, last_name = parse_github_name(info)
        return save_user(first_name, last_name, info["email"])
    except Exception as e:
        sys.stderr.write("%s\n" % e)
        flash(_("You have not defined a public email in your GitHub account, "
                "please <a href=\"http://github.com/settings/profile\">define "
                "it</a> and try again, or use one of the other options "
                "for signing up."))
        return redirect(url_for("try_tsuru"))


def parse_github_name(info):
    splitted = info["name"].split(" ")
    if len(splitted) > 1:
        return splitted[0], splitted[-1]
    return splitted[0], ""


@app.route("/tos", methods=["GET"])
def tos():
    return render_template("tos.html")


@app.route("/register/gplus", methods=["GET"])
def gplus_register():
    token = request.args.get("token")
    token_type = request.args.get("token_type")
    if token is None or token_type is None:
        return "Token is required.", 400
    headers = {"Authorization": "%s %s" % (token_type, token)}
    url = "{0}/userinfo?key={1}&userIp={2}".format(
        GOOGLE_OAUTH_ENDPOINT,
        GOOGLE_API_KEY,
        GOOGLE_USER_IP
    )
    resp = requests.get(url, headers=headers)
    info = resp.json()
    return save_user(info["given_name"], info["family_name"],
                     info["email"], redirect_to=url_for("index"))


def has_token(form):
    if "access_token" not in form.keys():
        return False
    if not form["access_token"] or form["access_token"] == "":
        return False
    return True


@app.before_request
def before_request():
    g.conn, g.db = connect_db()


@app.teardown_request
def teardown_request(exception):
    if hasattr(g, "conn"):
        g.conn.close()


def connect_db():
    mongo_uri_port = MONGO_URI.split(":")
    host = mongo_uri_port[0]
    port = int(mongo_uri_port[1])
    conn = pymongo.Connection(host, port)
    return conn, conn[MONGO_DATABASE_NAME]
