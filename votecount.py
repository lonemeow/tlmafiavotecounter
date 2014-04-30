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
        'vote'        : Template('$voter'),
        'unvote'      : Template('-$voter-'),
        'player'      : Template('$player ($count): $votes'),
        'not_voting'  : Template('Not voting ($count): $players'),
        'log_message' : Template('$level: $message ($url)'),
        }

bbcode_templates = {
        'vote'        : Template('$voter'),
        'unvote'      : Template('[s]$voter[/s]'),
        'player'      : Template('[b]$player[/b] ($count): $votes'),
        'not_voting'  : Template('[b]Not voting[/b] ($count): $players'),
        'log_message' : Template('$level: $message ([url=$url]post[/url])'),
        }

vote_pattern = re.compile('^##? ?Vote[: ] *(.*)$', re.IGNORECASE)
unvote_pattern = re.compile('^##? ?Unvote.*$', re.IGNORECASE)

log_messages = []
max_fuzz = 0.8
allow_self_vote = True

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
        self.players = players
        self.votes_by_target = defaultdict(list)
        self.votes_by_voter = defaultdict(lambda: None)

    def vote(self, voter, vote, url):
        target = find_matching_player(vote, self.players)

        if not allow_self_vote and voter == target:
            target = None

        if not target:
            log_message('error', '%s voted invalid player %s' % (voter, vote), url)
            return

        # Voted without unvoting first
        if self.votes_by_voter[voter]:
            log_message('warning', '%s changed vote without unvote' % voter, url)
            self.unvote(voter, url)

        if vote != target:
            log_message('vote', '%s voted %s (%s)' % (voter, vote, target), url)
        else:
            log_message('vote', '%s voted %s' % (voter, target), url)

        self.votes_by_target[target].append(self.Vote(voter))
        self.votes_by_voter[voter] = target

    def unvote(self, voter, url):
        target = self.votes_by_voter[voter]
        if target:
            log_message('vote', '%s unvoted %s' % (voter, target), url)

            for vote in reversed(self.votes_by_target[target]):
                if vote.voter == voter:
                    vote.unvoted = True
                    break

            self.votes_by_voter[voter] = None
        else:
            log_message('warning', '%s unvoted without vote' % voter, url)

    def dump(self, templates):
        for player, votes in self.votes_by_target.iteritems():
            print templates['player'].substitute(player=player, count=sum(map(lambda x: x.count(), votes)), votes=', '.join(map(lambda x: x.dump(templates), votes)))

        not_voting = [v for k, v in self.players.iteritems() if not self.votes_by_voter[v] and k == v.lower()]
        if not_voting:
            print
            print templates['not_voting'].substitute(count=len(not_voting), players=', '.join(not_voting))


def find_matching_player(vote, players):
    matches = difflib.get_close_matches(vote.lower(), players.keys(), cutoff=max_fuzz)

    if not matches:
        return None

    player = players[matches[0]]

    for match in matches[1:]:
        if player != players[match]:
            return None

    return player

class LogEntry:
    def __init__(self, severity, message, url):
        self.severity = severity
        self.message = message
        self.url = url

    def dump(self):
        return templates['log_message'].substitute(level=self.severity.upper(), message=self.message, url=self.url)


def log_message(severity, message, url):
    log_messages.append(LogEntry(severity, message, url))


def count_votes(url, state):
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
            if post_user not in state.players.values():
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
                        target = vote_match.group(1).strip()
                        state.vote(post_user, target, post_url)
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
    argparser.add_argument('--max-fuzz', type=float, default=max_fuzz, help='max fuzz factor used in player name matching')
    argparser.add_argument('--bbcode', action='store_true', help='output TL forum compatible BBCode')
    argparser.add_argument('--no-self-vote', action='store_true', help='disallow self voting')
    args = argparser.parse_args()

    players = []

    if args.players:
        if args.players[0] == '@':
            with open(args.players[1:]) as f:
                players = dict()

                for line in f:
                    aliases = [a.strip() for a in line.split(',')]

                    name = aliases[0]
                    aliases = aliases[1:]

                    players[name.lower()] = name

                    for a in aliases:
                        players[a.lower()] = name
        else:
            players = dict([(n.lower(), n) for n in args.players.split(',')])

    max_fuzz = args.max_fuzz

    if args.no_self_vote:
        allow_self_vote = False

    url = args.url
    state = GameState(players)
    while url:
        url = count_votes(url, state)

    templates = console_templates

    if args.bbcode:
        templates = bbcode_templates

    state.dump(templates)

    if log_messages:
        print

        for entry in log_messages:
            print entry.dump()
