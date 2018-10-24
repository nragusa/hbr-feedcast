import boto3
import json
import os
import sys
from datetime import datetime
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr

HERE = os.path.dirname(os.path.realpath(__file__))
SITE_PKGS = os.path.join(HERE, 'site-packages')
sys.path.append(SITE_PKGS)

import feedparser
from elasticsearch import Elasticsearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth
# from aws_xray_sdk.core import xray_recorder
# from aws_xray_sdk.core import patch_all

# Global variables passed in as environment variables
FEED_URL = os.environ['feedurl']
DDB_TABLE = os.environ['ddb']
MIN_SCORE = int(os.environ['minscore'])
ES_HOST = os.environ['esdomain']

# boto3 clients
ddb = boto3.client('dynamodb')
ddb_resource = boto3.resource('dynamodb')
table = ddb_resource.Table(DDB_TABLE)
comprehend = boto3.client('comprehend')

# Credentials to be used when adding to Elasticsearch index
credentials = boto3.Session().get_credentials()
awsauth = AWS4Auth(credentials.access_key, 
                    credentials.secret_key, 
                    boto3.session.Session().region_name,
                    'es', session_token=credentials.token)
es = Elasticsearch(
    hosts = [{'host': ES_HOST, 'port': 443}],
    http_auth = awsauth,
    use_ssl = True,
    verify_certs = True,
    connection_class = RequestsHttpConnection
)

def analyze(entry):
    """Takes the entry from the RSS feed and passes the content (description)
    of the podcast episode into Amazon Comprehend for entity analysis. If 
    an entity is returned with a confidence greater than MIN_SCORE, the
    entity, along with the episode title and published date is added to the
    Elasticsearch cluster."""
    entities = comprehend.detect_entities(
        Text=entry['content'][0]['value'],
        LanguageCode='en'
    )
    index_entries = {}
    for entity in entities['Entities']:
        if (entity['Score'] * 100) < MIN_SCORE:
            print('Entity has a confidence score below threshold')
        elif entity['Text'] in index_entries:
            print('Entity already in index')
        else:
            entity['title'] = entry['title']
            entity['published'] = entry['published']
            print('Adding {} into elasticsearch domain'.format(entity))
            # PUT into ES domain
            es.index(
                index='hbrfeedcast',
                doc_type='_doc',
                body=entity
            )

def add_to_ddb(entry):
    """Adds this entry into the DynamoDB table"""
    # Thu, 31 Aug 2006 13:10:00 -0500
    d = datetime.strptime(entry['published'], '%a, %d %b %Y %H:%M:%S %z')
    pub_date = d.strftime('%Y-%m-%d')
    pub_time = d.strftime('%H:%M:%S')
    try:
        episode = entry['title'].split(':')[0]
    except IndexError:
        episode = '0000'
    try:
        response = ddb.put_item(
            TableName=DDB_TABLE,
            Item={
                'title': {
                    'S': entry['title']
                },
                'pub_date': {
                    'S': pub_date
                },
                'pub_time': {
                    'S': pub_time
                },
                'episode': {
                    'S': episode
                },
                'published': {
                    'S': entry['published']
                },
                'link': {
                    'S': entry['link']
                },
                'author': {
                    'S': entry['author']
                },
                'content': {
                    'S': entry['content'][0]['value']
                }
            }
        )
    except ClientError as error:
        print('Problem updating DynamoDB: {}'.format(error))
        # TO DO: Raise error here?
    

def already_added(title):
    """Checks if the entry is already in DyanomDB. Returns True if found,
    otherwise returns False."""
    response = table.query(
        KeyConditionExpression=Key('title').eq(title)
    )
    if response['Count'] == 0:
        return False
    else:
        return True

def main(event, context):
    """Calls the HBR IdeaCast RSS feed and parses all of the entries. For each
    entry found, checks if we already saved this episode, and if not, adds it
    to DynamoDB, analyzes it with Comprehend, and sends the results to
    Elasticsearch. """
    feed = feedparser.parse(FEED_URL)
    total_added = 0
    try:
        total_to_add = int(event['max'])
    except KeyError:
        total_to_add = 10
    for entry in reversed(feed['entries']):
        if total_added == total_to_add:
            break
        if already_added(entry['title']):
            continue
        else:
            # add to DDB
            add_to_ddb(entry)
            # analyze description with comprehend
            analyze(entry)
            total_added += 1