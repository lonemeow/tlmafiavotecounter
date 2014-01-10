#!/usr/bin/env python

import re
import difflib
import argparse

from string import Template
from collections import defaultdict
from urlparse import urlsplit, urljoin
from urllib2 import urlopen
from BeautifulSoup import BeautifulSoup, NavigableString

console_templates = {
        'vote'       : Template('$voter'),
        'unvote'     : Template('-$voter-'),
        'player'     : Template('$player ($count): $votes'),
        'not_voting' : Template('Not voting ($count): $players')
        }

bbcode_templates = {
        'vote'       : Template('$voter'),
        'unvote'     : Template('[s]$voter[/s]'),
        'player'     : Template('[b]$player[/b] ($count): $votes'),
        'not_voting' : Template('[b]Not voting[/b] ($count): $players')
        }

vote_pattern = re.compile('^## ?Vote[: ] *(.*)$', re.IGNORECASE)
unvote_pattern = re.compile('^## ?Unvote.*$', re.IGNORECASE)

debug = False

log_messages = []

class GameState:
    class Vote:
        def __init__(self, voter):
            self.voter = voter
            self.unvoted = False

        def count(self):
            if not self.unvoted:
                return 1
            else:
                return 0

        def dump(self, templates):
            if not self.unvoted:
                return templates['vote'].substitute(voter=self.voter)
            else:
                return templates['unvote'].substitute(voter=self.voter)

    def __init__(self, players):
        self.players = list(players)
        self.votes_by_target = defaultdict(list)
        self.votes_by_voter = defaultdict(lambda: None)

    def vote(self, voter, target, url):
        log('debug', '%s vote %s' % (voter, target), url)
        # Voted without unvoting first
        if self.votes_by_voter[voter]:
            log('warning', 'Changed vote without unvote by %s: %s -> %s' % (voter, self.votes_by_voter[voter], target), url)
            self.unvote(voter, None)

        self.votes_by_target[target].append(self.Vote(voter))
        self.votes_by_voter[voter] = target

    def unvote(self, voter, url):
        target = self.votes_by_voter[voter]
        log('debug', '%s unvote %s' % (voter, target), url)
        if target:
            for vote in reversed(self.votes_by_target[target]):
                if vote.voter == voter:
                    vote.unvoted = True
                    break

            self.votes_by_voter[voter] = None
        else:
            log('warning', 'Unvote without vote by %s' % voter, url)

    def dump(self, templates):
        for player, votes in self.votes_by_target.iteritems():
            print templates['player'].substitute(player=player, count=sum(map(lambda x: x.count(), votes)), votes=', '.join(map(lambda x: x.dump(templates), votes)))

        not_voting = [v for v in self.players if not self.votes_by_voter[v]]
        if not_voting:
            print
            print templates['not_voting'].substitute(count=len(not_voting), players=', '.join(not_voting))


def find_matching_player(vote, players, max_fuzz):
    matches = difflib.get_close_matches(vote, players, cutoff=max_fuzz)

    if len(matches) != 1:
        return None
    else:
        return matches[0]

class LogEntry:
    def __init__(self, severity, message, url):
        self.severity = severity
        self.message = message
        self.url = url

    def dump(self):
        print '%s: %s (%s)' % (self.severity.upper(), self.message, self.url)


def log(severity, message, url):
    if severity == 'debug' and debug:
        print '%s: %s (%s)' % (severity.upper(), message, url)
    elif severity != 'debug':
        log_messages.append(LogEntry(severity, message, url))


def count_votes(url, max_fuzz, state):
    response = urlopen(url)
    url = response.geturl()
    fragment = urlsplit(url).fragment
    page = response.read()

    soup = BeautifulSoup(page)

    posts = soup.findAll('td', attrs={ 'class' : 'forumPost'})
    for post in posts:
        post_base = post.parent.parent.parent.parent.parent.parent
        post_anchor = post_base.a
        post_url = urljoin(url, '#' + post_anchor['name'])

        # If we have a fragment, ignore everything up to and including it
        if fragment:
            if post_anchor['name'] == fragment:
                fragment = None
        else:
            user_link = post_base.find('a', attrs={ 'data-user' : True })
            post_user = user_link['data-user']

            # Ignore posts by non-players
            if post_user not in state.players:
                continue

            for element in post:
                texts = []

                if isinstance(element, NavigableString):
                    texts = [ element ]
                elif element.name == 'b':
                    texts = [t for t in element if isinstance(t, NavigableString)]

                for text in texts:
                    vote_match = vote_pattern.match(text)
                    unvote_match = unvote_pattern.match(text)
                    if vote_match:
                        vote_data = vote_match.group(1)
                        target = find_matching_player(vote_data.strip(), state.players, max_fuzz)
                        if target:
                            state.vote(post_user, target, post_url)
                        else:
                            log('error', 'Invalid vote by %s: %s' % (post_user, vote_data), post_url)
                    elif unvote_match:
                        state.unvote(post_user, post_url)

    next_link = soup.find('a', rel='next')
    if next_link:
        next_url = urljoin(url, next_link['href'])
        return next_url
    else:
        return None


if __name__ == '__main__':
    argparser = argparse.ArgumentParser(description='Count the votes in a Team Liquid forum mafia game.')
    argparser.add_argument('url', help='The URL to start from (if this points to a specific message, the message and any messages before it on the page are excluded)')
    argparser.add_argument('--players', help='comma separated list of players or @filename.txt')
    argparser.add_argument('--max-fuzz', type=float, default=0.7, help='max fuzz factor used in player name matching')
    argparser.add_argument('--debug', action='store_true', help='enable debug messages')
    argparser.add_argument('--bbcode', action='store_true', help='output TL forum compatible BBCode')
    args = argparser.parse_args()

    players = []

    debug = args.debug

    if args.players:
        if args.players[0] == '@':
            with open(args.players[1:]) as f:
                players = [p.strip() for p in f]
        else:
            players = args.players.split(',')

    url = args.url
    state = GameState(players)
    while url:
        url = count_votes(url, args.max_fuzz, state)

    if not args.bbcode:
        state.dump(console_templates)
    else:
        state.dump(bbcode_templates)

    if log_messages:
        print

        for entry in log_messages:
            entry.dump()
