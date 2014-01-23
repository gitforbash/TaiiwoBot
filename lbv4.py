import socket, urllib2, sys, json, os, thread, re
configFile = open ('./linkbot.conf','rw')#	Import settings file
config = json.loads( configFile.read() )#	Parse config file
# Creating socket
s = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
print "[-]Connecting to server..."
try:
        s.connect(( config['settings']['host'] , int(config['settings']['port']))) #connect to server

except:
        print "[E]Could not connect to server."

s.send('nick ' + config['settings']['botNick'] + '\r\n')
s.send('user ' + config['settings']['botIdent'] + ' * ' + config['settings']['botUser'] + ' ' + config['settings']['botName'] + '\r\n')
for channel in config['settings']['joinChannels']:#	Join all the channels
        s.send('join ' + channel + '\r\n')
        s.send('privmsg ' + channel + ' :' + config['settings']['joinMessage'] + "\n\r")

def runPlugins(plugins, path, data):#	This function is for threading
	global s
	for plugin in plugins:
		exec(path + plugin)
		toSend = main(data)
		if toSend and toSend != '' and toSend != None:
			s.send(toSend)

loop = 0
while 1:
	config = configFile.open()#		Refresh the config file
	recvLen = int(config['settings']['recvLen'])
	recvData = s.recv(recvLen)
	data = {'recv' : recvData,#		Format data to send to the plugins.	
			'config' : config,
			'loop' : loop } 
	#Run plugins from ./plugins/
	path = './plugins/'
	rootPlugins = os.listdir('./plugins/')# 	Get plugin filenames only from the plugins directory
						#	This runs the plugins no matter what.
	thread.start_new_thread( runPlugins, (rootPlugins, path, data) )#	Run the root plugins in a new thread

	'''Check if the recv is a privmsg from a channel (Not foolproof, you can involk this by privmsging
	the bot with ":a!b@c PRIVMSG #d:e" for example.'''
	if not re.find(':*!*@* PRIVMSG #*:*') == -1 or not re.find(':*!*@* privmsg #*:*') == -1:
		#Run plugins from ./plugins/privmsg/*
		privMsgChanPlugins = []
		privMsgChanPaths = []
		for root, subFolders, files in os.walk('./plugins/privmsg/'):#		Fetch plugins recurisively. This means
			thread.start_new_thread( runPlugins, (files, root, data) )#	you can organize plugins in subfolders
										#	however you'd like. eg. Have a folder
										#	full of entertainment plugins that you
										#	can easily disable by prepending '.'
										#	to the folder name.
	# If recv is a private message to the bot
	elif not re.find(':*!*@* PRIVMSG ' + config['settings']['botNick'] + ' :*') == -1 or not re.find(':*!*@* privmsg ' + config['settings']['botNick'] + ' :*') == -1:
		# Run plugins from ./plugins/privmsgbot/*
		privMsgBotPlugins = []
		privMsgBotPaths = []
		for root, subFolders, files in os.walk('./plugins/privmsgbot/'):
			thread.start_new_thread( runPlugins, (files, root, data) )
	elif 'PING ' == recvData[0:4]:
		s.send('PONG ' + recvData.split(' ')[1][1:] + '\r\n')
		'''	I was thinking about making the bot run plugins from a 'PING' folder,
			but saw very little point, other than possible data logging?. Regardless,
			I left it out.	'''

	loop += 1
