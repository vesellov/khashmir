## Copyright 2002 Andrew Loewenstern, All Rights Reserved

from const import reactor
import time

from ktable import KTable, K
from knode import KNode as Node

from hash import newID, intify

from twisted.web import xmlrpc
from twisted.internet.defer import Deferred
from twisted.python import threadable
threadable.init()

from bsddb3 import db ## find this at http://pybsddb.sf.net/
from bsddb3._db import DBNotFoundError

# don't ping unless it's been at least this many seconds since we've heard from a peer
MAX_PING_INTERVAL = 60 * 15 # fifteen minutes

# concurrent FIND_NODE/VALUE requests!
N = 3



# this is the main class!
class Khashmir(xmlrpc.XMLRPC):
    __slots__ = ['listener', 'node', 'table', 'store', 'app']
    def __init__(self, host, port):
	self.node = Node(newID(), host, port)
	self.table = KTable(self.node)
	from twisted.internet.app import Application
	from twisted.web import server
	self.app = Application("xmlrpc")
	self.app.listenTCP(port, server.Site(self))
	self.store = db.DB()
	self.store.open(None, None, db.DB_BTREE)
	

    def render(self, request):
	"""
	    Override the built in render so we can have access to the request object!
	    note, crequest is probably only valid on the initial call (not after deferred!)
	"""
	self.crequest = request
	return xmlrpc.XMLRPC.render(self, request)

	
    #######
    #######  LOCAL INTERFACE    - use these methods!
    def addContact(self, host, port):
	"""
	 ping this node and add the contact info to the table on pong!
	"""
	n =Node(" "*20, host, port)  # note, we 
	self.sendPing(n)


    ## this call is async!
    def findNode(self, id, callback, errback=None):
	""" returns the contact info for node, or the k closest nodes, from the global table """
	# get K nodes out of local table/cache, or the node we want
	nodes = self.table.findNodes(id)
	d = Deferred()
	d.addCallbacks(callback, errback)
	if len(nodes) == 1 and nodes[0].id == id :
	    d.callback(nodes)
	else:
	    # create our search state
	    state = FindNode(self, id, d.callback)
	    reactor.callFromThread(state.goWithNodes, nodes)
    
    
    ## also async
    def valueForKey(self, key, callback):
	""" returns the values found for key in global table """
	nodes = self.table.findNodes(key)
	# create our search state
	state = GetValue(self, key, callback)
	reactor.callFromThread(state.goWithNodes, nodes)


    ## async, but in the current implementation there is no guarantee a store does anything so there is no callback right now
    def storeValueForKey(self, key, value):
	""" stores the value for key in the global table, returns immediately, no status 
	    in this implementation, peers respond but don't indicate status to storing values
	    values are stored in peers on a first-come first-served basis
	    this will probably change so more than one value can be stored under a key
	"""
	def _storeValueForKey(nodes, key=key, value=value, response= self._storedValueHandler, default= lambda t: "didn't respond"):
	    for node in nodes:
		if node.id != self.node.id:
		    df = node.storeValue(key, value, self.node.senderDict())
		    df.addCallbacks(response, default)
	# this call is asynch
	self.findNode(key, _storeValueForKey)
	
	
    def insertNode(self, n):
	"""
	insert a node in our local table, pinging oldest contact in bucket, if necessary
	
	If all you have is a host/port, then use addContact, which calls this method after
	receiving the PONG from the remote node.  The reason for the seperation is we can't insert
	a node into the table without it's peer-ID.  That means of course the node passed into this
	method needs to be a properly formed Node object with a valid ID.
	"""
	old = self.table.insertNode(n)
	if old and (time.time() - old.lastSeen) > MAX_PING_INTERVAL and old.id != self.node.id:
	    # the bucket is full, check to see if old node is still around and if so, replace it
	    
	    ## these are the callbacks used when we ping the oldest node in a bucket
	    def _staleNodeHandler(oldnode=old, newnode = n):
		""" called if the pinged node never responds """
		self.table.replaceStaleNode(old, newnode)
	
	    def _notStaleNodeHandler(sender, old=old):
		""" called when we get a ping from the remote node """
		if sender['id'] == old.id:
		    self.table.insertNode(old)

	    df = old.ping()
	    df.addCallbacks(_notStaleNodeHandler, self._staleNodeHandler)


    def sendPing(self, node):
	"""
	    ping a node
	"""
	df = node.ping(self.node.senderDict())
	## these are the callbacks we use when we issue a PING
	def _pongHandler(sender, id=node.id, host=node.host, port=node.port, table=self.table):
	    if id != 20 * ' ' and id != sender['id']:
		# whoah, got response from different peer than we were expecting
		pass
	    else:
		#print "Got PONG from %s at %s:%s" % (`msg['id']`, t.target.host, t.target.port)
		n = Node(sender['id'], host, port)
		table.insertNode(n)
	    return
	def _defaultPong(err):
	    # this should probably increment a failed message counter and dump the node if it gets over a threshold
	    return	

	df.addCallbacks(_pongHandler,_defaultPong)


    def findCloseNodes(self):
	"""
	    This does a findNode on the ID one away from our own.  
	    This will allow us to populate our table with nodes on our network closest to our own.
	    This is called as soon as we start up with an empty table
	"""
	id = self.node.id[:-1] + chr((ord(self.node.id[-1]) + 1) % 256)
	def callback(nodes):
	    pass
	self.findNode(id, callback)

    def refreshTable(self):
	"""
	    
	"""
	def callback(nodes):
	    pass

	for bucket in self.table.buckets:
	    if time.time() - bucket.lastAccessed >= 60 * 60:
		id = randRange(bucket.min, bucket.max)
		self.findNode(id, callback)
	
 
    #####
    ##### INCOMING MESSAGE HANDLERS
    
    def xmlrpc_ping(self, sender):
	"""
	    takes sender dict = {'id', <id>, 'port', port} optional keys = 'ip'
	    returns sender dict
	"""
	ip = self.crequest.getClientIP()
	n = Node(sender['id'], ip, sender['port'])
	self.insertNode(n)
	return self.node.senderDict()
		
    def xmlrpc_find_node(self, target, sender):
	nodes = self.table.findNodes(target)
	nodes = map(lambda node: node.senderDict(), nodes)
	ip = self.crequest.getClientIP()
	n = Node(sender['id'], ip, sender['port'])
	self.insertNode(n)
	return nodes, self.node.senderDict()
    
    def xmlrpc_store_value(self, key, value, sender):
	if not self.store.has_key(key):
	    self.store.put(key, value)
	ip = self.crequest.getClientIP()
	n = Node(sender['id'], ip, sender['port'])
	self.insertNode(n)
	return self.node.senderDict()
	
    def xmlrpc_find_value(self, key, sender):
    	ip = self.crequest.getClientIP()
	n = Node(sender['id'], ip, sender['port'])
	self.insertNode(n)
	if self.store.has_key(key):
	    return {'values' : self.store[key]}, self.node.senderDict()
	else:
	    nodes = self.table.findNodes(msg['key'])
	    nodes = map(lambda node: node.senderDict(), nodes)
	    return {'nodes' : nodes}, self.node.senderDict()

    ###
    ### message response callbacks
    # called when we get a response to store value
    def _storedValueHandler(self, sender):
	pass

	
    
    

class ActionBase:
    """ base class for some long running asynchronous proccesses like finding nodes or values """
    def __init__(self, table, target, callback):
	self.table = table
	self.target = target
	self.int = intify(target)
	self.found = {}
	self.queried = {}
	self.answered = {}
	self.callback = callback
	self.outstanding = 0
	self.finished = 0
	
	def sort(a, b, int=self.int):
	    """ this function is for sorting nodes relative to the ID we are looking for """
	    x, y = int ^ a.int, int ^ b.int
	    if x > y:
		return 1
	    elif x < y:
		return -1
	    return 0
	self.sort = sort
    
    def goWithNodes(self, t):
	pass
	
	

FIND_NODE_TIMEOUT = 15

class FindNode(ActionBase):
    """ find node action merits it's own class as it is a long running stateful process """
    def handleGotNodes(self, args):
	l, sender = args
	if self.finished or self.answered.has_key(sender['id']):
	    # a day late and a dollar short
	    return
	self.outstanding = self.outstanding - 1
	self.answered[sender['id']] = 1
	for node in l:
	    if not self.found.has_key(node['id']):
		n = Node(node['id'], node['host'], node['port'])
		self.found[n.id] = n
		self.table.insertNode(n)
	self.schedule()
		
    def schedule(self):
	"""
	    send messages to new peers, if necessary
	"""
	if self.finished:
	    return
	l = self.found.values()
	l.sort(self.sort)

	for node in l[:K]:
	    if node.id == self.target:
		self.finished=1
		return self.callback([node])
	    if not self.queried.has_key(node.id) and node.id != self.table.node.id:
		#xxxx t.timeout = time.time() + FIND_NODE_TIMEOUT
		df = node.findNode(self.target, self.table.node.senderDict())
		df.addCallbacks(self.handleGotNodes, self.defaultGotNodes)
		self.outstanding = self.outstanding + 1
		self.queried[node.id] = 1
	    if self.outstanding >= N:
		break
	assert(self.outstanding) >=0
	if self.outstanding == 0:
	    ## all done!!
	    self.finished=1
	    reactor.callFromThread(self.callback, l[:K])
	
    def defaultGotNodes(self, t):
	if self.finished:
	    return
	self.outstanding = self.outstanding - 1
	self.schedule()
	
	
    def goWithNodes(self, nodes):
	"""
	    this starts the process, our argument is a transaction with t.extras being our list of nodes
	    it's a transaction since we got called from the dispatcher
	"""
	for node in nodes:
	    if node.id == self.table.node.id:
		continue
	    self.found[node.id] = node
	    #xxx t.timeout = time.time() + FIND_NODE_TIMEOUT
	    df = node.findNode(self.target, self.table.node.senderDict())
	    df.addCallbacks(self.handleGotNodes, self.defaultGotNodes)
	    self.outstanding = self.outstanding + 1
	    self.queried[node.id] = 1
	if self.outstanding == 0:
	    self.callback(nodes)


GET_VALUE_TIMEOUT = 15
class GetValue(FindNode):
    """ get value task """
    def handleGotNodes(self, args):
	l, sender = args
	l = l[0]
	if self.finished or self.answered.has_key(sender['id']):
	    # a day late and a dollar short
	    return
	self.outstanding = self.outstanding - 1
	self.answered[sender['id']] = 1
	# go through nodes
	# if we have any closer than what we already got, query them
	if l.has_key('nodes'):
	    for node in l['nodes']:
		if not self.found.has_key(node['id']):
		    n = Node(node['id'], node['host'], node['port'])
		    self.found[n.id] = n
		    self.table.insertNode(n)
	elif l.has_key('values'):
	    ## done
	    self.finished = 1
	    return self.callback(l['values'])
	self.schedule()
		
    ## get value
    def schedule(self):
	if self.finished:
	    return
	l = self.found.values()
	l.sort(self.sort)

	for node in l[:K]:
	    if not self.queried.has_key(node.id) and node.id != self.table.node.id:
		#xxx t.timeout = time.time() + GET_VALUE_TIMEOUT
		df = node.getValue(node, self.target)
		df.addCallbacks(self.handleGotNodes, self.defaultGotNodes)
		self.outstanding = self.outstanding + 1
		self.queried[node.id] = 1
	    if self.outstanding >= N:
		break
	assert(self.outstanding) >=0
	if self.outstanding == 0:
	    ## all done, didn't find it!!
	    self.finished=1
	    reactor.callFromThread(self.callback,[])
    
    ## get value
    def goWithNodes(self, nodes):
	for node in nodes:
	    if node.id == self.table.node.id:
		continue
	    self.found[node.id] = node
	    #xxx t.timeout = time.time() + FIND_NODE_TIMEOUT
	    df = node.findNode(self.target, self.table.node.senderDict())
	    df.addCallbacks(self.handleGotNodes, self.defaultGotNodes)
	    self.outstanding = self.outstanding + 1
	    self.queried[node.id] = 1
	if self.outstanding == 0:
	    reactor.callFromThread(self.callback, [])



#------

def test_build_net(quiet=0):
    from whrandom import randrange
    import thread
    port = 2001
    l = []
    peers = 16
    
    if not quiet:
	print "Building %s peer table." % peers
	
    for i in xrange(peers):
	a = Khashmir('localhost', port + i)
	l.append(a)
    
    def run(l=l):
	while(1):
		events = 0
		for peer in l:
			events = events + peer.dispatcher.runOnce()
		if events == 0:
			time.sleep(.25)

    thread.start_new_thread(l[0].app.run, ())
    for peer in l[1:]:
	peer.app.run()
	
    for peer in l[1:]:
	n = l[randrange(0, len(l))].node
	peer.addContact(n.host, n.port)
	n = l[randrange(0, len(l))].node
	peer.addContact(n.host, n.port)
	n = l[randrange(0, len(l))].node
	peer.addContact(n.host, n.port)
	
    time.sleep(5)

    for peer in l:
	peer.findCloseNodes()
    time.sleep(5)
    for peer in l:
	peer.refreshTable()
    return l
        
def test_find_nodes(l, quiet=0):
    import threading, sys
    from whrandom import randrange
    flag = threading.Event()
    
    n = len(l)
    
    a = l[randrange(0,n)]
    b = l[randrange(0,n)]
    
    def callback(nodes, l=l, flag=flag):
	if (len(nodes) >0) and (nodes[0].id == b.node.id):
	    print "test_find_nodes	PASSED"
	else:
	    print "test_find_nodes	FAILED"
	flag.set()
    a.findNode(b.node.id, callback)
    flag.wait()
    
def test_find_value(l, quiet=0):
    from whrandom import randrange
    from sha import sha
    import time, threading, sys
    
    fa = threading.Event()
    fb = threading.Event()
    fc = threading.Event()
    
    n = len(l)
    a = l[randrange(0,n)]
    b = l[randrange(0,n)]
    c = l[randrange(0,n)]
    d = l[randrange(0,n)]

    key = sha(`randrange(0,100000)`).digest()
    value = sha(`randrange(0,100000)`).digest()
    if not quiet:
	print "inserting value...",
	sys.stdout.flush()
    a.storeValueForKey(key, value)
    time.sleep(3)
    print "finding..."
    
    def mc(flag, value=value):
	def callback(values, f=flag, val=value):
	    try:
		if(len(values) == 0):
		    print "find                FAILED"
		else:
		    if values[0]['value'] != val:
			print "find                FAILED"
		    else:
			print "find                FOUND"
	    finally:
		f.set()
	return callback
    b.valueForKey(key, mc(fa))
    c.valueForKey(key, mc(fb))
    d.valueForKey(key, mc(fc))
    
    fa.wait()
    fb.wait()
    fc.wait()
    
if __name__ == "__main__":
    l = test_build_net()
    time.sleep(3)
    print "finding nodes..."
    test_find_nodes(l)
    test_find_nodes(l)
    test_find_nodes(l)
    print "inserting and fetching values..."
    test_find_value(l)
    test_find_value(l)
    test_find_value(l)
    test_find_value(l)
    test_find_value(l)
    test_find_value(l)
