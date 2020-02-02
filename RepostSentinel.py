import os, praw, psycopg2, time
from sys import stdout
import sys
from PIL import Image
import logging
import yaml
import requests
import prawcore
import urllib3.exceptions


class RepostSentinel:
    def __init__(self, **kwargs):
        self.db_connection = None
        self.subredditSettings = None
        self.logger = None
        self.debug = False
        self.config = yaml.safe_load(open('config.yml'))
        super(RepostSentinel, self).__init__(**kwargs)

    def start(self):
        self.setup_logging()

        client_id = self.config['CLIENT_ID']
        client_secret = self.config['CLIENT_SECRET']
        username = self.config['USER_NAME']
        password = self.config['USER_PASS']
        user_agent = self.config['USER_AGENT']

        # DB Connection

        try:
            self.db_connection = psycopg2.connect(f"dbname='{self.config['DB_NAME']}' user='{self.config['DB_USER']}' "
                                                  f"host='{self.config['DB_HOST']}' password='{self.config['DB_PASS']}'"
                                                  )
            self.db_connection.autocommit = True
        except Exception as e:
            self.logger.critical('Error connecting to DB: \n{}'.format(e))
            sys.exit(1)

        # Connect to reddit

        try:
            r = praw.Reddit(client_id=client_id, client_secret=client_secret, password=password, user_agent=user_agent,
                            username=username)
        except Exception as e:
            self.logger.error('Error connecting to reddit: \n{}'.format(e))
            sys.exit(1)

        # ----------- MAIN LOOP ----------- #
        while True:
            self.logger.info("Starting Main Loop")
            try:
                self.loadSubredditSettings()
                if self.subredditSettings:
                    for settings in self.subredditSettings:
                        if settings[1] is False:
                            self.ingestFull(r, settings)
                            self.loadSubredditSettings()
                        if settings[1]:
                            self.ingestNew(r, settings)

                    self.checkMail(r)

            except(
                    prawcore.exceptions.ResponseException,
                    prawcore.exceptions.RequestException,
                    prawcore.exceptions.ServerError,
                    urllib3.exceptions.TimeoutError,
                    requests.exceptions.Timeout,
            ):
                self.logger.warning(
                    "HTTP Requests Error. Likely on reddits end due to site issues."
                )
                time.sleep(300)
            except prawcore.exceptions.InvalidToken:
                self.logger.warning(
                    "API Token Error. Likely on reddits end. Issue self-resolves."
                )
                time.sleep(180)
            except prawcore.exceptions.BadJSON:
                self.logger.warning(
                    "PRAW didn't get good JSON, probably reddit sending bad data due to site issues."
                )
                time.sleep(180)
            except praw.exceptions.APIException:
                self.logger.error("PRAW/Reddit API Error")
                time.sleep(30)
            except praw.exceptions.ClientException:
                self.logger.error("PRAW Client Error")
                time.sleep(30)
            except KeyboardInterrupt as e:
                self.logger.warning("Caught KeyboardInterrupt - Exiting")
                sys.exit()
            except Exception:
                self.logger.critical("General Exception - Sleeping 5 min")
                time.sleep(300)

    # Setup console logger
    def setup_logging(self):
        self.logger = logging.getLogger("RepostSentinal")
        formatter = logging.Formatter('[%(asctime)s %(levelname)s] %(message)s')
        # Prevent default handler from being used
        self.logger.propagate = False
        console_handler = logging.StreamHandler(stream=stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.DEBUG)
        self.logger.addHandler(console_handler)

        if self.debug:
            self.logger.setLevel(logging.DEBUG)
        else:
            self.logger.setLevel(logging.INFO)

    # Import new submissions
    def ingestNew(self, r, settings):
        self.logger.info('Scanning new for /r/{0}'.format(settings[0]))
        for submission in r.subreddit(settings[0]).new(limit=200):
            self.logger.debug('Processing submission {}'.format(submission.fullname))
            self.indexSubmission(r, submission, settings, True)

    # Import all submissions from all time within a sub
    def ingestFull(self, r, settings):
        for topall in r.subreddit(settings[0]).top(time_filter='all'):
            self.logger.info(
                f"ingestfull of topall found submission {topall.fullname} for r/{settings[0]}"
            )
            self.indexSubmission(r, topall, settings, False)
        for topyear in r.subreddit(settings[0]).top(time_filter='year'):
            self.logger.info(
                f"ingestfull of topyear found submission {topyear.fullname} for r/{settings[0]}"
            )
            self.indexSubmission(r, topyear, settings, False)
        for topmonth in r.subreddit(settings[0]).top(time_filter='month'):
            self.logger.info(
                f"ingestfull of topmonth found submission {topmonth.fullname} for r/{settings[0]}"
            )
            self.indexSubmission(r, topmonth, settings, False)

            # Update DB

            cur = self.db_connection.cursor()
            cur.execute('UPDATE SubredditSettings SET imported=TRUE WHERE subname=\'{0}\''.format(settings[0]))

    def indexSubmission(self, r, submission, settings, enforce):
        self.logger.debug(f"Got connection for indexing submission {submission.fullname}")
        try:
            # Skip self posts
            if submission.is_self:
                self.logger.debug(
                f"skipping self post {submission.fullname} for r/{settings[0]}"
                )
                return

            cur = self.db_connection.cursor()

            # Check for an existing entry so we don't make a duplicate
            self.logger.debug(
            f"checking if post already in db {submission.fullname} for r/{settings[0]}"
            )
            cur.execute("SELECT id FROM Submissions WHERE id='{0}'".format(submission.id))
            results = cur.fetchone()

            if results:
                self.logger.debug(
                f"skipping post already in db {submission.fullname} for r/{settings[0]}"
                )
                return

            self.logger.info(f'Indexing submission: {submission.fullname}')

            # Download and process the media
            submissionProcessed = False

            media = str(submission.url.replace("m.imgur.com", "i.imgur.com")).lower()
            temp_file = '/tmp/temp_media_file'

            # Check url
            if (
                    (
                    media.endswith(".jpg")
                    or media.endswith(".jpg?1")
                    or media.endswith(".png")
                    or media.endswith("png?1")
                    or media.endswith(".jpeg")
                )
                or "reddituploads.com" in media
                or "reutersmedia.net" in media
                or "500px.org" in media
                or "redditmedia.com" in media
            ):

                try:
                    if os.path.isfile(temp_file):
                        os.remove(temp_file)

                    # Download it
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_5_8) AppleWebKit/534.50.2 (KHTML, like Gecko) Version/5.0.6 Safari/533.22.3'}
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

                        imgHash = self.DifferenceHash(img)

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
                                self.enforceSubmission(r, submission, settings, mediaData)

                            # Add to DB
                            cur.execute(
                                'INSERT INTO Media(hash, submission_id, subreddit, frame_number, frame_count, frame_width, frame_height, total_pixels, file_size) VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s)',
                                mediaData)
                            submissionProcessed = True

                    except Image.DecompressionBombError as e:
                        self.logger.warning('File aborting due to size {0} - {1}'.format(
                            submission.fullname, e
                            )
                        )
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
                    except Exception as e:
                        self.logger.error('Error processing {0} - {1}'.format(submission.fullname, e))
                except Exception as e:
                    self.logger.warning('Failed to download {0} - {1}'.format(submission.fullname, e))

            if os.path.isfile(temp_file):
                os.remove(temp_file)

            # Add submission to DB
            submissionDeleted = False
            if submission.author == '[deleted]':
                submissionDeleted = True

            try:
                removedStatus = submission.removed
            except:
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
                self.logger.error('Error adding {0}'.format(submission.id))
        except Exception as e:
            self.logger.error('Failed to ingest {0} - {1}'.format(submission.id, e))

    def enforceSubmission(self, r, submission, settings, mediaData):

        try:
            if submission.removed or submission.banned_by:
                return

            cur = self.db_connection.cursor()

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
                    if matchCount > 9:
                        break

                    mediaSimilarity = int(
                        ((64 - bin(mediaData[0] ^ int(mediaHash[0])).count('1')) * 100.0)
                        / 64.0
                    )

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
                        if originalSubmission.removed or originalSubmission.banned_by:
                            currentStatus = 'Removed'
                        elif originalSubmission.author == '[deleted]':
                            currentStatus = 'Deleted'

                        matchRows = matchRows + matchRowTemplate.format(
                            mediaParent[3],
                            self.convertDateFormat(mediaParent[2]),
                            str(mediaSimilarity),
                            str(mediaData[5]),
                            str(mediaData[6]),
                            mediaParent[5],
                            mediaParent[4],
                            mediaParent[0],
                            currentScore,
                            currentComments,
                            currentStatus
                        )

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

                    submission.report(
                        'Possible repost: {0} similar - {1} active'.format(
                            matchCount, matchCountActive)
                    )
                    replyInfo = submission.reply(
                        matchInfoTemplate.format(
                            submission.author,
                            mediaData[5],
                            mediaData[6],
                            mediaData[7],
                            mediaData[8],
                            matchRows)
                    )
                    try:
                        # TODO second line should be dropped?
                        replyInfo.mod.remove()
                        praw.models.reddit.comment.CommentModeration(replyInfo).remove(spam=False)
                    except prawcore.exceptions.Forbidden:
                        self.logger.warn('Bot missing perms to enforce submission: {}'.format(replyInfo.fullname))

                if blacklisted:
                    submission.mod.remove(spam=False)
                    replyRemove = submission.reply(settings[9])
                    replyRemove.distinguish(how='yes', sticky=True)

                if removeSubmission:
                    submission.mod.remove(spam=False)
                    replyRemove = submission.reply(settings[9])
                    replyRemove.distinguish(how='yes', sticky=True)


        except (prawcore.exceptions.ResponseException,
                prawcore.exceptions.RequestException,
                prawcore.exceptions.ServerError,
                urllib3.exceptions.TimeoutError,
                requests.exceptions.Timeout):
            self.logger.warn('HTTP Requests Error. Likely on reddits end due to site issues.')
            time.sleep(300)

        except prawcore.exceptions.InvalidToken:
            self.logger.warn('API Token Error. Likely on reddits end. Issue self-resolves.')
            time.sleep(180)

        except prawcore.exceptions.BadJSON:
            self.logger.warn('PRAW didn\'t get good JSON, probably reddit sending bad data due to site issues.')
            time.sleep(180)

        except praw.exceptions.APIException:
            self.logger.error('PRAW/Reddit API Error')
            time.sleep(30)

        except praw.exceptions.ClientException:
            self.logger.error('PRAW Client Error')
            time.sleep(30)

        except KeyboardInterrupt as e:
            self.logger.warn('Caught KeyboardInterrupt - Exiting')
            sys.exit()


    # Get settings of all subreddits from DB
    def loadSubredditSettings(self):
        cur = self.db_connection.cursor()
        cur.execute('SELECT * FROM SubredditSettings')
        self.subredditSettings = cur.fetchall()
        self.logger.info("Loaded subreddit settings table")

    # Check messages for blacklist requests
    def checkMail(self, r):
        try:
            self.logger.info("Getting Mail")
            for msg in r.inbox.unread(limit=None):
                if not isinstance(msg, praw.models.Message):
                    msg.mark_read()
                    continue

                if msg.subject.strip().lower().startswith("moderator message from"):
                    msg.mark_read()
                    continue

                if "You have been removed as a moderator from " in msg.body:
                    self.removeModStatus(msg)
                    continue

                if msg.subject == 'blacklist':
                    msg.mark_read()
                    submissionId = ''
                    if len(msg.body) == 6:
                        submissionId = msg.body
                    elif 'reddit.com' in msg.body and '/comments/' in msg.body:
                        submissionId = msg.body[msg.body.find('/comments/') + len('/comments/'):6]
                    elif 'redd.it' in msg.body:
                        submissionId = msg.body[msg.body.find('redd.it/') + len('redd.it/'):6]

                    if len(submissionId) == 6:
                        blacklistSubmission = r.submission(id=submissionId)
                        for settings in self.subredditSettings:
                            if settings[0] == blacklistSubmission.subreddit:
                                for moderator in r.subreddit(settings[0]).moderator():
                                    if msg.author == moderator:
                                        self.indexSubmission(r, blacklistSubmission, settings, False)
                                        cur = self.db_connection.cursor()
                                        cur.execute('UPDATE Submissions SET blacklist=TRUE WHERE id=\'{0}\''.format(
                                            submissionId))
                    else:
                        msg.mark_read()
                    continue

        except (Exception) as e:
            self.logger.error('Failed to check messages - {0}'.format(e))
        return

    def acceptModInvite(self, message):
        try:
            cur = self.db_connection.cursor()
            message.mark_read()
            message.subreddit.mod.accept_invite()

            cur.execute(
                "SELECT * FROM subredditsettings WHERE subname=%s",
                (str(message.subreddit),),
            )
            results = cur.fetchall()
            if results:
                cur.execute(
                    "UPDATE subredditsettings SET enabled=True WHERE subname=%s",
                    (str(message.subreddit),),
                )
            else:
                cur.execute(
                    "INSERT INTO subredditsettings (subname) VALUES(%s)",
                    (str(message.subreddit),),
                )
            self.logger.info("Accepted mod invite for /r/{}".format(message.subreddit))
        except Exception as e:
            self.logger.error(
                "Unable to accept mod invite and set sub settings for r/{}. ID: {}".format(
                    message.subreddit, message.fullname
                )
            )

    def removeModStatus(self, message):
        try:
            cur = self.db_connection.cursor()
            message.mark_read()
            cur.execute(
                "UPDATE subredditsettings SET enabled=False WHERE subname=%s",
                (str(message.subreddit),),
            )
            self.logger.info(f"Removed as mod in /r/{message.subreddit}")
        except Exception as e:
            self.logger.error(
                "Unable to update set sub settings removed status for r/{}. ID: {}".format(
                    message.subreddit, message.fullname
                )
            )

    # Hashing function
    def DifferenceHash(self, theImage):

        theImage = theImage.convert("L")
        theImage = theImage.resize((8, 8), Image.ANTIALIAS)
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

    @staticmethod
    def convertDateFormat(timestamp):

        return str(time.strftime('%B %d, %Y - %H:%M:%S', time.localtime(timestamp)))


if __name__ == '__main__':
    RepostSentinel.start()
