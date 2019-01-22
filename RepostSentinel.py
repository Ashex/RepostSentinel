import os, praw, psycopg2, time
from sys import stdout
import sys
from PIL import Image
import logging
import yaml
import requests
import prawcore
import urllib3.exceptions


conn = None
subredditSettings = None



def Main():

    # Logging
    global logger
    logger = setup_logging()


    # Declare global variables
    global conn
    global dbName
    global dbUser
    global dbHost
    global dbPasswrd

    global clientID
    global clientSecret
    global passwrd
    global userAgent
    global usernm

    #Get values for everything
    config = yaml.safe_load(open('config.yml'))

    dbName = config['DB_NAME']
    dbUser = config['DB_USER']
    dbHost = config['DB_HOST']
    dbPasswrd = config['DB_PASS']

    clientID = config['CLIENT_ID']
    clientSecret = config['CLIENT_SECRET']
    usernm = config['USER_NAME']
    passwrd = config['USER_PASS']
    userAgent = config['USER_AGENT']

    # DB Connection

    try:
        conn = psycopg2.connect("dbname='{0}' user='{1}' host='{2}' password='{3}'".format(dbName, dbUser, dbHost, dbPasswrd))
        conn.autocommit = True
    except Exception as e:
        logger.error('Error connecting to DB: \n{}'.format(e))
        return


    # Connect to reddit

    r = None
    try:
        r = praw.Reddit(client_id=clientID, client_secret=clientSecret, password=passwrd, user_agent=userAgent, username=usernm)
    except Exception as e:
        logger.error('Error connecting to reddit: \n{}'.format(e))



    global subredditSettings

    # ----------- MAIN LOOP ----------- #
    while True:

        try:

            loadSubredditSettings()

            if subredditSettings:

                for settings in subredditSettings:

                    if settings[1] == False:

                        ingestFull(r, settings)
                        loadSubredditSettings()

                    if settings[1]:

                        ingestNew(r, settings)

                checkMail(r)

            else:

                return

        except (Exception) as e:

            logger.error('Error on main loop - {0}'.format(e))



#Setup console logger
def setup_logging(debug=False):
    logger = logging.getLogger("RepostSentinal")
    formatter = logging.Formatter('[%(asctime)s %(levelname)s] %(message)s')
    # Prevent default handler from being used
    logger.propagate = False
    console_handler = logging.StreamHandler(stream=stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.DEBUG)
    logger.addHandler(console_handler)

    if debug:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    return logger


# Import new submissions
def ingestNew(r, settings):
    logger.info('Scanning new for /r/{0}'.format(settings[0]))

    for submission in r.subreddit(settings[0]).new(limit=200):
        logger.debug('Processing submission {}'.format(submission.fullname))
        indexSubmission(r, submission, settings, True)




# Import all submissions from all time within a sub
def ingestFull(r, settings):

    for topall in r.subreddit(settings[0]).top(time_filter='all'):
        indexSubmission(r, topall, settings, False)
    for topyear in r.subreddit(settings[0]).top(time_filter='year'):
        indexSubmission(r, topyear, settings, False)
    for topmonth in r.subreddit(settings[0]).top(time_filter='month'):
        indexSubmission(r, topmonth, settings, False)

        # Update DB
        global conn
        cur = conn.cursor()
        cur.execute('UPDATE SubredditSettings SET imported=TRUE WHERE subname=\'{0}\''.format(settings[0]))




def indexSubmission(r, submission, settings, enforce):
    try:
        # Skip self posts
        if submission.is_self:
            return

        global conn
        cur = conn.cursor()

        # Check for an existing entry so we don't make a duplicate
        cur.execute("SELECT id FROM Submissions WHERE id='{0}'".format(submission.id))
        results = cur.fetchone()

        if results:
            return

        logger.info('Indexing submission: {}'.format(submission.fullname))

        # Download and process the media
        submissionProcessed = False

        media = str(submission.url.replace("m.imgur.com", "i.imgur.com")).lower()
        temp_file = '/tmp/temp_media_file'

        # Check url
        if (media.endswith(".jpg") or media.endswith(".jpg?1") or media.endswith(".png") or media.endswith(
                "png?1") or media.endswith(
                ".jpeg")) or "reddituploads.com" in media or "reutersmedia.net" in media or "500px.org" in media or "redditmedia.com" in media:

            try:

                if os.path.isfile(temp_file):
                    os.remove(temp_file)


                # Download it
                headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_5_8) AppleWebKit/534.50.2 (KHTML, like Gecko) Version/5.0.6 Safari/533.22.3'}
                response = requests.get(media, headers=headers)
                mediaContent = response.content

                # Save it
                f = open(temp_file, 'wb')
                f.write(mediaContent)
                f.close()

                try:
                    img = Image.open(temp_file)

                    width, height = img.size
                    pixels = width * height
                    size = os.path.getsize(temp_file)

                    imgHash = DifferenceHash(img)

                    mediaData = (
                        imgHash,
                        str(submission.id),
                        settings[0],
                        1,
                        1,
                        width,
                        height,
                        pixels,
                        size
                    )

                    if width > 200 and height > 200:
                        if enforce:
                            enforceSubmission(r, submission, settings, mediaData)

                        # Add to DB
                        cur.execute(
                            'INSERT INTO Media(hash, submission_id, subreddit, frame_number, frame_count, frame_width, frame_height, total_pixels, file_size) VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s)',
                            mediaData)
                        submissionProcessed = True

                except Image.DecompressionBombError:
                    logger.warning('File aborting due to size {0} - {1}'.format(submission.fullname, e))
                    submissionValues = (
                        str(submission.id),
                        settings[0],
                        float(submission.created),
                        str(submission.title),
                        str(submission.url),
                        int(submission.num_comments),
                        int(submission.score)
                    )
                    cur.execute(
                        'INSERT INTO Submissions(id, subreddit, timestamp, title, url, comments, score) VALUES(%s, %s, %s, %s, %s, %s, %s)',
                        submissionValues)
                    return
                except (Exception) as e:
                    logger.error('Error processing {0} - {1}'.format(submission.fullname, e))
            except (Exception) as e:
                logger.warning('Failed to download {0} - {1}'.format(submission.fullname, e))
        try:
            os.remove(temp_file)
        except:
            pass

        # Add submission to DB
        submissionDeleted = False
        if submission.author == '[deleted]':
            submissionDeleted = True

        try:
            removedStatus = submission.removed
        except Exception as e:
            removedStatus = False

        submissionValues = (
            str(submission.id),
            settings[0],
            float(submission.created),
            str(submission.author),
            str(submission.title),
            str(submission.url),
            int(submission.num_comments),
            int(submission.score),
            submissionDeleted,
            removedStatus,
            str(submission.removal_reason),
            False,
            submissionProcessed
        )

        try:
            cur.execute(
                'INSERT INTO Submissions(id, subreddit, timestamp, author, title, url, comments, score, deleted, removed, removal_reason, blacklist, processed) VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                submissionValues)
        except:
            logger.error('Error adding {0}'.format(submission.id))
    except (Exception) as e:
        logger.error('Failed to ingest {0} - {1}'.format(submission.id, e))



def enforceSubmission(r, submission, settings, mediaData):

    try:

        if submission.removed:
            return

        global conn
        cur = conn.cursor()

        # Check if it's the generic 'deleted image' from imgur
        if mediaData[0] == '9925021303884596990':

            submission.report('Image removed from imgur.')

            return

        # Handle single images
        if mediaData[4] == 1:

            cur.execute('SELECT * FROM Media WHERE frame_count=1 AND subreddit=\'{0}\''.format(settings[0]))
            mediaHashes = cur.fetchall()

            matchInfoTemplate = '**OP:** {0}\n\n**Image Stats:**\n\n* Width: {1}\n\n* Height: {2}\n\n* Pixels: {3}\n\n* Size: {4}\n\n**History:**\n\nUser | Date | Match % | Image | Title | Karma | Comments | Status\n:---|:---|:---|:---|:---|:---|:---|:---\n{5}'
            matchRowTemplate = '/u/{0} | {1} | {2}% | [{3} x {4}]({5}) | [{6}](https://redd.it/{7}) | {8} | {9} | {10}\n'
            matchCount = 0
            matchCountActive = 0
            matchRows = ''
            reportSubmission = False
            removeSubmission = False
            blacklisted = False
            sameAuthor = False

            # Find matches
            for mediaHash in mediaHashes:

                mediaSimilarity = int(((64 - bin(mediaData[0] ^ int(mediaHash[0])).count('1'))*100.0)/64.0)

                parentBlacklist = False

                # Report threshold
                if mediaSimilarity > settings[6]:

                    cur.execute('SELECT * FROM Submissions WHERE id=\'{0}\''.format(mediaHash[1]))
                    mediaParent = cur.fetchone()
                    parentBlacklist = mediaParent[11]

                    originalSubmission = r.submission(id=mediaParent[0])



                    currentScore = int(originalSubmission.score)
                    currentComments = int(originalSubmission.num_comments)
                    currentStatus = 'Active'
                    if originalSubmission.removed:
                        currentStatus = 'Removed'
                    elif originalSubmission.author == '[deleted]':
                        currentStatus = 'Deleted'

                    matchRows = matchRows + matchRowTemplate.format(mediaParent[3], convertDateFormat(mediaParent[2]), str(mediaSimilarity), str(mediaData[5]), str(mediaData[6]), mediaParent[5], mediaParent[4], mediaParent[0], currentScore, currentComments, currentStatus)

                    matchCount = matchCount + 1

                    if currentStatus == 'Active':
                        matchCountActive = matchCountActive + 1

                    reportSubmission = True

                    if mediaParent[3] == submission.author:
                        sameAuthor = True

                # Remove threshold
                if mediaSimilarity > settings[8]:

                    removeSubmission = True

                    # TODO: Add comment count and karma as thresholds



                # Blacklist
                if mediaSimilarity == 100 and parentBlacklist:

                    blacklisted = True

            # Only report if the submission author is different
            if reportSubmission and sameAuthor is False:

                submission.report('Possible repost: {0} similar - {1} active'.format(matchCount, matchCountActive))
                replyInfo = submission.reply(matchInfoTemplate.format(submission.author, mediaData[5], mediaData[6], mediaData[7], mediaData[8], matchRows))
                try:
                    praw.models.reddit.comment.CommentModeration(replyInfo).remove(spam=False)
                except prawcore.exceptions.Forbidden:
                    logger.warn('Bot missing perms to enforce submission: {}'.format(replyInfo.fullname))

            if blacklisted:

                submission.remove(spam=False)
                replyRemove = submission.reply(settings[9])
                replyRemove.distinguish(how='yes', sticky=True)

            if removeSubmission:

                submission.remove(spam=False)
                replyRemove = submission.reply(settings[9])
                replyRemove.distinguish(how='yes', sticky=True)


    except (prawcore.exceptions.ResponseException,

            prawcore.exceptions.RequestException,

            prawcore.exceptions.ServerError,

            urllib3.exceptions.TimeoutError,

            requests.exceptions.Timeout):

        logger.warn('HTTP Requests Error. Likely on reddits end due to site issues.')

        time.sleep(300)

    except prawcore.exceptions.InvalidToken:

        logger.warn('API Token Error. Likely on reddits end. Issue self-resolves.')

        time.sleep(180)

    except prawcore.exceptions.BadJSON:

        logger.warn('PRAW didn\'t get good JSON, probably reddit sending bad data due to site issues.')

        time.sleep(180)

    except praw.exceptions.APIException:

        logger.error('PRAW/Reddit API Error')

        time.sleep(30)

    except praw.exceptions.ClientException:

        logger.error('PRAW Client Error')

        time.sleep(30)

    except KeyboardInterrupt as e:

        logger.warn('Caught KeyboardInterrupt - Exiting')

        sys.exit()


    return



# Get settings of all subreddits from DB
def loadSubredditSettings():

    global conn
    global subredditSettings

    cur = conn.cursor()
    cur.execute('SELECT * FROM SubredditSettings')
    subredditSettings = cur.fetchall()


    return




# Check messages for blacklist requests
def checkMail(r):

    try:

        for msg in r.inbox.unread(mark_read=True):

            if msg.subject == 'blacklist':

                submissionId = ''

                if len(msg.body) == 6:
                    submissionId = msg.body
                elif 'reddit.com' in msg.body and '/comments/' in msg.body:
                    submissionId = msg.body[msg.body.find('/comments/') + len('/comments/'):6]
                elif 'redd.it' in msg.body:
                    submissionId = msg.body[msg.body.find('redd.it/') + len('redd.it/'):6]

                if len(submissionId) == 6:

                    blacklistSubmission = r.submission(id=submissionId)

                    for settings in subredditSettings:

                        if settings[0] == blacklistSubmission.subreddit:

                            for moderator in r.subreddit(settings[0]).moderator():

                                if msg.author == moderator:

                                    indexSubmission(r, blacklistSubmission, settings, False)

                                    global conn
                                    cur = conn.cursor()
                                    cur.execute('UPDATE Submissions SET blacklist=TRUE WHERE id=\'{0}\''.format(submissionId))


    except (Exception) as e:

        logger.error('Failed to check messages - {0}'.format(e))


    return




# Hashing function
def DifferenceHash(theImage):

    theImage = theImage.convert("L")
    theImage = theImage.resize((8,8), Image.ANTIALIAS)
    previousPixel = theImage.getpixel((0, 7))
    differenceHash = 0

    for row in range(0, 8, 2):

        for col in range(8):
            differenceHash <<= 1
            pixel = theImage.getpixel((col, row))
            differenceHash |= 1 * (pixel >= previousPixel)
            previousPixel = pixel

        row += 1

        for col in range(7, -1, -1):
            differenceHash <<= 1
            pixel = theImage.getpixel((col, row))
            differenceHash |= 1 * (pixel >= previousPixel)
            previousPixel = pixel


    return differenceHash



def convertDateFormat(timestamp):

    return str(time.strftime('%B %d, %Y - %H:%M:%S', time.localtime(timestamp)))



Main()
