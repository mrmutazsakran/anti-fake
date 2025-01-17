# import flask dependencies
from flask import Flask
from flask import jsonify, request, make_response, render_template
import tensorflow as tf
from util import *
from webscrape_helper import azureClaimSearch
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import Dial, VoiceResponse, Say
from twilio.rest import Client
import time
import random
import pickle
from REP import *
import pandas as pd
# from ..rep_model.REP import * 

from werkzeug.contrib.cache import SimpleCache


import os

os.environ['KMP_DUPLICATE_LIB_OK']='True'

# initialize the flask app
app = Flask(__name__)
sess = tf.Session()

#TWILIO API
account_sid = 'ACd607c4799217f7d61cc2e38ce0302948'
auth_token = 'f7fcf72fe1ba5169f53e4ba0f24ebcda'
client = Client(account_sid, auth_token)

# define cache
cache = SimpleCache()



def getCacheItem(claim):
    return cache.get(claim.lower())

def storeCacheItem(claim, score, out_urls):
    rv = {"urls": out_urls, "score": score, "articles": 100}
    cache.set(claim.lower(), rv, timeout=5 * 60)

def createCacheItem(urls, score):
    return score, urls

def initializeDummyCache():
    # storeCacheItem("donald trump secured wall funding", createCacheItem())
    storeCacheItem("obama is not american", ['https://www.independent.co.uk/news/world/americas/us-politics/donald-trump-birther-barack-obama-born-conspiracy-senator-claim-private-a8083566.html'], -0.0403)
    storeCacheItem("50 dead in india whatsapp fake news", ['https://www.bbc.com/news/world-asia-india-45140158'], .1403)
    storeCacheItem("jeff bezos got divorced", ['https://www.bbc.com/news/world-asia-india-45140158'], 0.1403)
    return

# Getting Parameters
def getParameters():
    parameters = []
    # parameters.append(request.args.get('male'))
    return parameters


# Cross origin support
def sendResponse(responseObj):
    response = jsonify(responseObj)
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Methods', 'GET')
    response.headers.add('Access-Control-Allow-Headers', 'accept,content-type,Origin,X-Requested-With,Content-Type,access_token,Accept,Authorization,source')
    response.headers.add('Access-Control-Allow-Credentials', True)
    return response


def trainVectors():
    file_train_instances = "train_stances.csv"
    file_train_bodies = "train_bodies.csv"
    file_test_instances = "test_stances_unlabeled.csv"
    file_test_bodies = "test_bodies.csv"

    # Initialise hyperparameters
    r = random.Random()
    lim_unigram = 5000

    # Load data sets
    raw_train = FNCData(file_train_instances, file_train_bodies)
    raw_test = FNCData(file_test_instances, file_test_bodies)

    # Process data sets
    _, _, bow_vectorizer, tfreq_vectorizer, tfidf_vectorizer = \
        pipeline_train(raw_train, raw_test, lim_unigram=lim_unigram)
    
    storeVector(bow_vectorizer, "bow.pickle")
    storeVector(tfreq_vectorizer, "tfreq.pickle")
    storeVector(tfidf_vectorizer, "tfidf.pickle")

def runPredictions():
    # call train vectors only at the first time
    # trainVectors()
    bow_vectorizer = loadVector("bow.pickle")
    tfreq_vectorizer = loadVector( "tfreq.pickle")
    tfidf_vectorizer = loadVector("tfidf.pickle")

    # hardcode the number of features
    feature_size = 10001
    target_size = 4
    hidden_size = 100

    # Create placeholders
    features_pl = tf.placeholder(tf.float32, [None, feature_size], 'features')
    keep_prob_pl = tf.placeholder(tf.float32)

    # Infer batch size
    batch_size = tf.shape(features_pl)[0]

    # Define multi-layer perceptron
    hidden_layer = tf.nn.dropout(tf.nn.relu(tf.contrib.layers.linear(features_pl, hidden_size)), keep_prob=keep_prob_pl)
    logits_flat = tf.nn.dropout(tf.contrib.layers.linear(hidden_layer, target_size), keep_prob=keep_prob_pl)
    logits = tf.reshape(logits_flat, [batch_size, target_size])

    # Define prediction
    softmaxed_logits = tf.nn.softmax(logits)
    predict = tf.argmax(softmaxed_logits, 1)

    sess = tf.Session()
    print("Loading checkpoint")
    load_model(sess)

    '''PREDICTION'''
    print("Now running predictions...")

    userClaims = "./claims.csv"
    userBodies = "./bodies.csv"
    # parse that info
    raw_test = FNCData(userClaims, userBodies)
    # TODO hotload the vector representations instead of calculating every time
    test_set = pipeline_test(raw_test, bow_vectorizer, tfreq_vectorizer, tfidf_vectorizer)

    test_feed_dict = {features_pl: test_set, keep_prob_pl: 1.0}
    # run predictions
    test_pred = sess.run(predict, feed_dict=test_feed_dict)
    # timing
    print("Stance detections complete: {}".format(test_pred))
    # store in a csv
    save_predictions(test_pred, "pred.csv" )
    return test_pred

def runPipeline(claim):
    start_time = time.time()

    cache = getCacheItem(claim)
    print(cache)
    if cache is not None:
        return cache['score'], cache['urls'], cache['articles']

    # webscrape
    azureClaimSearch(claim)

    # run model
    stances = runPredictions()

    # load the articles using panda
    df_articles = pd.read_csv("articles.csv")
    df_stances = pd.read_csv("pred.csv")
    df_ml = pd.concat([df_articles, df_stances], axis=1)

    number_of_articles = len(df_articles.index)

    # calculate score using reputation
    score, out_urls  = returnOutput(df_ml)

    # store 
    print("Total response time--- %s seconds ---" % (time.time() - start_time))

    # clean up for next search
    print("Cleaning up claims bodies and articles, and caching results")
    os.remove("./claims.csv")
    os.remove("./bodies.csv")
    os.remove("./articles.csv")

    storeCacheItem(claim, score, out_urls)
    return score, out_urls, number_of_articles

# API for prediction
@app.route("/predict", methods=["GET", "POST"])
def predict():
    claim = request.args.get('claim')
    score, out_urls, _ = runPipeline(claim)
    return sendResponse({"claim": claim, "score": score,  \
    "sources": out_urls})


# default route
@app.route('/')
def index():
    # return render_template("index.html")
    return "Hello"

# default route
@app.route('/test',  methods=['POST'])
def test():
    # req = request.get_json(force=True)
    # Use this data in your application logic
    # from_number = request.form['From']
    # to_number = request.form['To']
    # body = request.form['Body']
    # print(request.form['Body'])


    call = client.calls.create(
                        url='http://demo.twilio.com/docs/voice.xml',
                        to='+18723561437',
                        from_='+18882116027'
                    )

    print(call.sid)

    return str(call)


def formatOutputUrls(urls):
    base = "Here are some relevant sources:"
    for url in urls:
        base += ' ' + url + ' '

    return base

# function for responses
def results(claim):
    # build a request object
    #req = request.get_json(force=True)

    # claim = request.args.get('claim')
    # if claim == None:=

    # run pipeline to get back score and relevant urls
    score, out_urls, number_of_articles = runPipeline(claim)
    out_urls = ['https://edition.cnn.com/2018/12/20/politics/donald-trump-shutdown-border-wall-funding/index.html', 'http://time.com/5486379/house-funding-trump-border-wall-shutdown/']

    if score > 0:
        verdict = "VERIFIED"
    else:
        verdict = "FALSE"

    formatted_urls = formatOutputUrls(out_urls)


    sentence =  "Your search was: '" + claim + "' | We referenced " + str(number_of_articles)  + " articles and our verdict about your stance is " + verdict + " | " + formatted_urls

    # appended_query = pd.DataFrame({'claim': [claim], 'score': [score], 'article1_source':[article1['source'] ], 'article1_url':[ article1['url'] ], 'article2_source':[ article2['source'] ],'article2_url':[ article2['url'] ],'article3_source':[ article3['source'] ],'article3_url':[ article3['url'] ] })
    # queries_df.append(appended_query)
    # queries_df.to_csv('queries.csv')

    return sentence

    #test each article against the action from google dialogflow
    # if action == "echo":
    #     response = {
    #         "fulfillmentText": sentence,
    #         "source" : "TruthAI",  
    #     } 
    #     return response
         
    # elif action == "webhook-intent":
    #     response = {
    #         "fulfillmentText": sentence,
    #         "source" : "TruthAI",  
    #     }
    #     # return a fulfillment response
    #     return response


# create a route for webhook

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    # return response
    resp = MessagingResponse()
    # Use this data in your application logic
    from_number = request.form['From']
    to_number = request.form['To']
    claim = request.form['Body']

    resp.message("your search was: " + claim)
    resp.message("we are searching the web to find you the truh. Give us a few seconds")
    
    sentence = results(claim)

    resp.message(sentence)

    return str(resp)


# run the app
if __name__ == '__main__':
    initializeDummyCache()
    app.run(debug=True, threaded=True)