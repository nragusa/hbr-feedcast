import boto3
import json
import os
import sys
from datetime import date
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr

HERE = os.path.dirname(os.path.realpath(__file__))
SITE_PKGS = os.path.join(HERE, 'site-packages')
sys.path.append(SITE_PKGS)

from elasticsearch import Elasticsearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth
# from aws_xray_sdk.core import xray_recorder
# from aws_xray_sdk.core import patch_all

# Global variables passed in as environment variables
ES_HOST = os.environ['esdomain']
DDB_TABLE = os.environ['ddb']
LATEST_INDEX = os.environ['latest_index']

# Credentials to be used when adding to Elasticsearch index
credentials = boto3.Session().get_credentials()
awsauth = AWS4Auth(credentials.access_key, 
                    credentials.secret_key, 
                    boto3.session.Session().region_name,
                    'es', session_token=credentials.token)
                    

""" General global configuation """
SKILL_NAME = 'HBR Feedcast'
HELP_MESSAGE = ('You can ask me, what are the latest episodes, or, if '
                'there are any episodes about collaboration, or you can say '
                'exit... What can I help you with?')
HELP_REPROMPT = 'What can I help you with?'
STOP_MESSAGE = 'OK. Thanks!'
FALLBACK_MESSAGE = ('The HBR Feedcast app can\'t help with that. I can, however,'
                    ' tell you if there are any episodes featuring Paul Levy. '
                    'What can I help you with?')
FALLBACK_REPROMPT = 'What can I help you with?'

def main(event, context):
    """This function is called upon every invocation
    of the Lambda function. It serves as the entry point
    from the Alexa Skill.

    Keyword arguments:
    event -- The details of this event
    context -- Other parameters associated to this event
    """
    if event['request']['type'] == 'LaunchRequest':
        return on_launch(event['request'])
    elif event['request']['type'] == 'IntentRequest':
        return on_intent(event['request'], event['session'])
    elif event['request']['type'] == 'SessionEndedRequest':
        return on_session_ended()


def on_intent(request, session):
    """Whenever a user has an intent, this funciton is
    invoked and will broker the intent to the appropriate
    function to fullfill the request.

    Keyword arguments:
    request -- The request from the user
    session -- Session data related to this interaction
    """

    intent_name = request['intent']['name']
    print(json.dumps(request))

    # process the intents
    if intent_name == 'GetLatestEpisodes':
        return get_latest_episodes()
    elif intent_name == 'GetEpisodeByNumber':
        return get_episode_by_number(request['intent']['slots'])
    elif intent_name == 'GetEpisodeByTitle':
        return get_episode_by_title(request['intent']['slots'])
    elif intent_name == 'PersonSearch':
        return search_episodes_by_person(request['intent']['slots'])
    elif intent_name == 'IdeaSearch':
        return search_episodes_by_idea(request['intent']['slots'])
    elif intent_name == 'AMAZON.HelpIntent':
        return get_help_response()
    elif (intent_name == 'AMAZON.StopIntent' or intent_name == 'AMAZON.NoIntent'):
        return get_stop_response()
    elif intent_name == 'AMAZON.CancelIntent':
        return get_stop_response()
    elif intent_name == 'AMAZON.FallbackIntent':
        return get_fallback_response()
    else:
        print('Invalid intent made, responding with help')
        return get_help_response()

def get_latest_episodes():
    """Called from -> GetLatestEpisodes
    
    Returns the titles of the last 3 published episodes
    """
    table = __get_table()
    try:
        items = table.scan(IndexName=LATEST_INDEX)
    except ClientError as error:
        print('Problem getting latest episodes: {}'.format(error))
        return speech_response('Sorry, I\'m having trouble connecting to my '
                               'services. Please try again later.', True)
    else:
        speech_output = 'I found the following episodes. '
        sorted_episodes = sorted(items['Items'], key=lambda k: k['pub_date'], reverse=True) 
        for item in sorted_episodes[:3]:
            episode = item['title'].split(':')[0]   # Gets episode number from title
            title = item['title'].split(':')[1]     # Gets the title
            speech_output += 'Episode {}, {}. '.format(episode, title)
        speech_output += 'Anything else today?'
        card_display = speech_output
        print(speech_output)
        return response(speech_response_with_card(SKILL_NAME, speech_output, 
                                                  card_display, False))

def get_episode_by_number(slots):
    """Called from -> GetEpisodeByNumber
    
    Returns the description of an episode by the episode number
    """
    print('In function get_episode_by_number({})'.format(json.dumps(slots)))
    episode = str(slots['episode_id']['value'])
    table = __get_table()
    try:
        episodes = table.scan(
            IndexName=LATEST_INDEX,
            FilterExpression=Attr('title').begins_with(episode)
        )
    except ClientError as error:
        print('Problem scanning DynamoDB: {}'.format(error))
        # TO DO: Return error response here
    else:
        if episodes['Count'] == 1:
            episode_details = get_episode_details(episodes['Items'][0]['title'])
            if not episode_details:
                speech_output = ('I\'m sorry, I couldn\'t find details on that '
                                 'episode. ')
            else:
                speech_output = 'I found the following on episode {}. {} '.format(episode, episode_details)
        else:
            speech_output = ('I\'m sorry, I couldn\'t find details on that '
                                 'episode. ')
    speech_output += 'Anything else today?'
    card_display = speech_output
    print(speech_output)
    return response(speech_response_with_card(SKILL_NAME, speech_output, 
                                              card_display, False))

def get_episode_by_title(slots):
    """Called from -> GetEpisodeByTitle
    
    Returns the description of an episode by the title
    """
    print('In function get_episode_by_title({})'.format(json.dumps(slots)))
    title = str(slots['episode_title']['value'])
    try:
        table = __get_table()
        episodes = table.scan(
            IndexName=LATEST_INDEX,
            FilterExpression=Attr('title').contains(title.title())
        )
    except ClientError as error:
        print('Problem scanning DynamoDB: {}'.format(error))
        # TO DO: Return response here
    else:
        if episodes['Count'] == 0:
            # Search cluster as fallback
            print('Searching elasticsearch cluster')
            es = Elasticsearch(
                hosts = [{'host': ES_HOST, 'port': 443}],
                http_auth = awsauth,
                use_ssl = True,
                verify_certs = True,
                connection_class = RequestsHttpConnection
            )
            #print(json.dumps(es.search(q=title)))
            search = es.search(q=title)
            if search['hits']:
                episode_details = get_episode_details(
                    search['hits']['hits'][0]['_source']['title']
                )
            if not episode_details:
                speech_output = ('I\'m sorry, I couldn\'t find details on that '
                                 'episode. ')
            else:
                episode = search['hits']['hits'][0]['_source']['title'].split(':')[0]
                speech_output = 'I found the following on episode {}. {} '.format(search['hits']['hits'][0]['_source']['title'], episode_details)
        else:
            # Look up the episode in the table
            episode_details = get_episode_details(episodes['Items'][0]['title'])
            if not episode_details:
                speech_output = ('I\'m sorry, I couldn\'t find details on that '
                                 'episode. ')
            else:
                episode = episodes['Items'][0]['title'].split(':')[0]
                speech_output = 'I found the following on episode {}. {} '.format(episode, episode_details)
    speech_output += 'Anything else today?'
    card_display = speech_output
    print(json.dumps(episodes))
    return response(speech_response_with_card(SKILL_NAME, speech_output, 
                                          card_display, False))
        

def search_episodes_by_person(slots):
    """Called from -> PersonSearch
    
    Returns episodes that may have a specific person in them
    """
    print('In function search_episodes_by_person({})'.format(json.dumps(slots)))
    person = str(slots['episode_person']['value'])
    print('Searching elasticsearch cluster')
    es = __get_cluster()
    results = es.search(q=person)
    print(json.dumps(results))
    if results['hits'] and len(results['hits']['hits']) > 0:
        speech_output = 'I found an episode with {} titled {} '.format(
            results['hits']['hits'][0]['_source']['Text'],
            results['hits']['hits'][0]['_source']['title']
        )
    else:
        speech_output = 'I didn\'t find any episodes with {}. '.format(person)
    speech_output += 'Anything else today?'
    card_display = speech_output
    # print(json.dumps(episodes))
    return response(speech_response_with_card(SKILL_NAME, speech_output, 
                                          card_display, False))

def search_episodes_by_idea(slots):
    """Called from -> IdeaSearch
    
    Returns episodes that may have a specific person in them
    """
    print('In function search_episodes_by_idea({})'.format(json.dumps(slots)))
    person = str(slots['episode_idea']['value'])
    print('Searching elasticsearch cluster')
    es = __get_cluster()
    results = es.search(q=person)
    print(json.dumps(results))
    if results['hits'] and len(results['hits']['hits']) > 0:
        speech_output = 'I found an episode about {} titled {} '.format(
            results['hits']['hits'][0]['_source']['Text'],
            results['hits']['hits'][0]['_source']['title']
        )
    else:
        speech_output = 'I didn\'t find any episodes about {}. '.format(person)
    speech_output += 'Anything else today?'
    card_display = speech_output
    # print(json.dumps(episodes))
    return response(speech_response_with_card(SKILL_NAME, speech_output, 
                                          card_display, False))

"""Intent helper functions"""

def get_episode_details(title):
    """Returns the details of an episode by the title"""
    print('Getting details for episode {}'.format(title))
    try:
        ddb = boto3.resource('dynamodb')
        table = ddb.Table(DDB_TABLE)
        details = table.query(
            KeyConditionExpression=Key('title').eq(title),
            ProjectionExpression='content',
            Limit=1
        )
    except ClientError as error:
        print('Problem getting content for episode: {}'.format(error))
        return False
    else:
        if details['Count'] == 1:
            return details['Items'][0]['content']
        else:
            print('No results returned from DynamoDB')
            return False

def __get_table():
    ddb = boto3.resource('dynamodb')
    table = ddb.Table(DDB_TABLE)
    return table

def __get_cluster():
    es = Elasticsearch(
        hosts = [{'host': ES_HOST, 'port': 443}],
        http_auth = awsauth,
        use_ssl = True,
        verify_certs = True,
        connection_class = RequestsHttpConnection
    )
    return es

def get_slot_id(slot):
    """Verifies that a known ID is in this slot. If the ID
    doesn't exist, then this is an unknown slot value and
    would cause the skill to fail. The intent will elicit
    the slot value again if this returns None.
    """
    if 'resolutions' not in slot:
        return
    for value in slot['resolutions']['resolutionsPerAuthority']:
        try:
            return value['values'][0]['value']['id']
        except KeyError:
            print('Unrecognized slot value found: {}'.format(json.dumps(slot)))


def get_slot_spoken_name(slot):
    """Returns the appropriate spoken name for the slot.
    This should always succeed as it should only be called
    when the slot is verified by it's ID.
    """
    if 'resolutions' not in slot:
        return
    for value in slot['resolutions']['resolutionsPerAuthority']:
        try:
            return value['values'][0]['value']['name']
        except KeyError:
            print('Unrecognized slot value found: {}'.format(json.dumps(slot)))


def get_help_response():
    """ get and return the help string  """

    speech_message = HELP_MESSAGE
    return response(speech_response_prompt(speech_message,
                                           speech_message, False))


def get_launch_response():
    """ get and return the help string  """

    return get_help_response()


def get_stop_response():
    """ end the session, user wants to quit the game """

    speech_output = STOP_MESSAGE
    return response(speech_response(speech_output, True))


def get_fallback_response():
    """ end the session, user wants to quit the game """

    speech_output = FALLBACK_MESSAGE
    return response(speech_response(speech_output, False))


def on_session_started():
    """" called when the session starts  """
    # print("on_session_started")


def on_session_ended():
    """ called on session ends """
    # print("on_session_ended")


def on_launch(request):
    """ called on Launch, we reply with a launch message  """

    return get_launch_response()


"""Speech response handlers"""


def speech_response(output, endsession):
    """  create a simple json response  """
    return {
        'outputSpeech': {
            'type': 'PlainText',
            'text': output
        },
        'shouldEndSession': endsession
    }


def elicit_response(speech_output, slot, endsession):
    return {
        'version': '1.0',
        'response': {
            'outputSpeech': {
                'type': 'PlainText',
                'text': speech_output
            },
            'directives': [
                {
                    'type': 'Dialog.ElicitSlot',
                    'slotToElicit': slot
                }
            ],
            'shouldEndSession': endsession
        }
    }


def dialog_response(endsession):
    """  create a simple json response with card """

    return {
        'version': '1.0',
        'response': {
            'directives': [
                {
                    'type': 'Dialog.Delegate'
                }
            ],
            'shouldEndSession': endsession
        }
    }


def speech_response_with_card(title, output, cardcontent, endsession):
    """  create a simple json response with card """

    return {
        'card': {
            'type': 'Simple',
            'title': title,
            'content': cardcontent
        },
        'outputSpeech': {
            'type': 'PlainText',
            'text': output
        },
        'shouldEndSession': endsession
    }


def response_ssml_text_and_prompt(output, endsession, reprompt_text):
    """ create a Ssml response with prompt  """

    return {
        'outputSpeech': {
            'type': 'SSML',
            'ssml': "<speak>" + output + "</speak>"
        },
        'reprompt': {
            'outputSpeech': {
                'type': 'SSML',
                'ssml': "<speak>" + reprompt_text + "</speak>"
            }
        },
        'shouldEndSession': endsession
    }


def ssml_response_with_card(title, output, cardcontent, endsession):
    """  create a simple json response with card """

    return {
        'card': {
            'type': 'Simple',
            'title': title,
            'content': cardcontent
        },
        'outputSpeech': {
            'type': 'SSML',
            'ssml': "<speak>" + output + "</speak>"
        },
        'shouldEndSession': endsession
    }


def speech_response_prompt(output, reprompt_text, endsession):
    """ create a simple json response with a prompt """

    return {
        'outputSpeech': {
            'type': 'PlainText',
            'text': output
        },
        'reprompt': {
            'outputSpeech': {
                'type': 'PlainText',
                'text': reprompt_text
            }
        },
        'shouldEndSession': endsession
    }


def response(speech_message):
    """ create a simple json response  """
    return {
        'version': '1.0',
        'response': speech_message
    }
